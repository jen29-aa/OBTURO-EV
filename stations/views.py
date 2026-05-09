from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.utils import timezone
from django.db import models
from django.db.models import Q
from django.db import transaction
from datetime import datetime, timedelta
import math
import numpy as np
import threading
import asyncio
import json
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .models import (
    ChargingStation,
    Booking,
    FavouriteStation,
    PeerCharger,
    PeerBooking,
    StationRating,
    UserPenalty,
    Waitlist,
    StationReport,
)
from .serializers import (
    ChargingStationSerializer,
    BookingSerializer,
    FavouriteStationSerializer,
    UserProfileSerializer,
    PeerChargerSerializer,
    PeerBookingSerializer,
)
from .email_service import send_booking_confirmation_email, send_waitlist_notification_email
from accounts.models import UserCar, DeviceToken
from .firebase import send_push_notification

# Waitlist service (centralized)
from .waitlist_service import (
    promote_waitlist_for_station,
    get_waitlist_info,
    reorder_waitlist,
)

# -------------------------
# Utility: penalty helper
# -------------------------
def add_penalty(user, points):
    """
    Corrected add_penalty that updates penalty_points and optionally blocks user.
    Ensure your UserPenalty model has 'penalty_points' and optional 'blocked_until'.
    """
    pen, _ = UserPenalty.objects.get_or_create(user=user)
    # Some models use 'penalty_points' name — adjust if your model uses different attr
    if hasattr(pen, "penalty_points"):
        pen.penalty_points += points
    else:
        # fallback to 'points' if older model
        pen.points = getattr(pen, "points", 0) + points

    # Blocking rules (example thresholds)
    try:
        # Keep blocks very short; max 5 minutes
        if pen.penalty_points >= 3:
            pen.blocked_until = timezone.now() + timedelta(minutes=5)
    except Exception:
        # if blocked_until field not present, ignore
        pass

    pen.save()

# -------------------------
# Utility: broadcast slot update via WebSocket
# -------------------------
def broadcast_slot_update(station_id):
    """Broadcast slot update to all WebSocket clients for a station"""
    try:
        channel_layer = get_channel_layer()
        station = ChargingStation.objects.get(id=station_id)
        
        now = timezone.now()
        active_bookings = Booking.objects.filter(
            station=station,
            start_time__lte=now,
            end_time__gte=now,
            status='active'
        ).count()
        
        data = {
            'id': station.id,
            'name': station.name,
            'available_slots': station.available_slots,
            'total_slots': station.total_slots,
            'active_bookings': active_bookings,
            'status': station.status,
            'price_per_kwh': float(station.price_per_kwh),
            'power_kw': float(station.power_kw),
            'timestamp': timezone.now().isoformat(),
        }
        
        async_to_sync(channel_layer.group_send)(
            f'station_{station_id}',
            {
                'type': 'slots_update',
                'data': data
            }
        )
    except Exception as e:
        print(f"[Broadcast Error] Failed to broadcast slot update: {e}")

# -------------------------
# Utility: distance
# -------------------------
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371  # km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )

    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# -------------------------
# Utility: route proximity
# -------------------------
def is_station_near_route(station, route_points, max_distance_km):
    for pt in route_points:
        d = calculate_distance(pt["lat"], pt["lng"], station.latitude, station.longitude)
        if d <= max_distance_km:
            return True
    return False


# -------------------------
# Utility: TOPSIS
# -------------------------
def topsis(matrix, weights, impacts):
    matrix = np.array(matrix, dtype=float)
    weights = np.array(weights, dtype=float)

    # Normalize with zero-safety
    norm = np.linalg.norm(matrix, axis=0)
    norm[norm == 0] = 1
    nm = matrix / norm

    # Weighting
    wm = nm * weights

    impacts_arr = np.array(impacts)

    # Ideal best & worst
    ideal_best = np.where(impacts_arr == "+", wm.max(axis=0), wm.min(axis=0))
    ideal_worst = np.where(impacts_arr == "+", wm.min(axis=0), wm.max(axis=0))

    # Distances
    d_best = np.sqrt(((wm - ideal_best) ** 2).sum(axis=1))
    d_worst = np.sqrt(((wm - ideal_worst) ** 2).sum(axis=1))

    denom = d_best + d_worst
    denom[denom == 0] = 1

    return (d_worst / denom).tolist()


# -------------------------
# DRY: nearby stations
# -------------------------
def _get_nearby_station_tuples(user_lat: float, user_lng: float, radius_km: float):
    result = []
    for st in ChargingStation.objects.all():
        dist = calculate_distance(user_lat, user_lng, st.latitude, st.longitude)
        if dist <= radius_km:
            result.append((st, dist))
    result.sort(key=lambda x: x[1])
    return result


# -------------------------
# Utility: Calculate real-time available slots
# -------------------------
def get_available_slots_now(station):
    """
    Calculate available slots RIGHT NOW based on active bookings.
    Returns the number of slots available at the current moment.
    """
    now = timezone.now()
    # Count bookings that are currently active (overlapping with now)
    active_count = Booking.objects.filter(
        station=station,
        status="active",
        start_time__lte=now,
        end_time__gt=now
    ).count()
    return max(0, station.total_slots - active_count)


def get_available_slots_at_time(station, start_time, end_time):
    """
    Calculate available slots for a specific time range.
    Returns the number of slots available during the requested period.
    """
    # Count bookings that overlap with the requested time window
    overlapping = Booking.objects.filter(
        station=station,
        status="active",
        start_time__lt=end_time,
        end_time__gt=start_time,
    ).count()
    return max(0, station.total_slots - overlapping)


# -------------------------
# Utility: parse iso datetime (returns timezone-aware in settings tz)
# -------------------------
def parse_iso_datetime(dt_str):
    """
    Accepts ISO-like strings (e.g., '2025-12-07T12:15' or '2025-12-07T12:15:00').
    Converts to timezone-aware datetime using project timezone.
    """
    # Python's fromisoformat can parse 'YYYY-MM-DDTHH:MM[:SS][.ffffff][+HH:MM]'
    dt = datetime.fromisoformat(dt_str)
    tz = timezone.get_current_timezone()
    if dt.tzinfo is None:
        return timezone.make_aware(dt, tz)
    return dt.astimezone(tz)


# ---------------------------------------------------------
# 1. GET ALL STATIONS
# ---------------------------------------------------------
@api_view(["GET"])
def get_all_stations(request):
    stations = ChargingStation.objects.all().order_by("id")
    return Response(ChargingStationSerializer(stations, many=True).data)


# ---------------------------------------------------------
# 2. NEARBY STATIONS (detailed – for list view)
# ---------------------------------------------------------
@api_view(["GET"])
def get_nearby_stations(request):
    try:
        user_lat = float(request.GET.get("lat"))
        user_lng = float(request.GET.get("lng"))
    except (TypeError, ValueError):
        return Response({"error": "lat and lng are required"}, status=400)

    radius = float(request.GET.get("radius", 10))  # km

    station_tuples = _get_nearby_station_tuples(user_lat, user_lng, radius)

    found = []
    for st, dist in station_tuples:
        item = ChargingStationSerializer(st).data
        item["distance_km"] = round(dist, 2)
        found.append(item)

    return Response({"count": len(found), "stations": found})


# ---------------------------------------------------------
# 3. CREATE BOOKING (with auto-waitlist)
# ---------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_booking(request):
    user = request.user
   # ---------------------------------------------------------
    # BLOCKED USER VALIDATION
    # ---------------------------------------------------------
    from django.utils import timezone

    pen = UserPenalty.objects.filter(user=user).first()


    if pen and pen.blocked_until and pen.blocked_until > timezone.now():
        # Cap any existing block to max 5 minutes from now
        max_block = timezone.now() + timedelta(minutes=5)
        if pen.blocked_until > max_block:
            pen.blocked_until = max_block
            try:
                pen.save(update_fields=["blocked_until"])
            except Exception:
                pen.save()

        # Display in local timezone (Asia/Kolkata) to avoid UTC confusion
        local_block = timezone.localtime(pen.blocked_until)
        return Response(
        {
            "error": f"You are blocked until {local_block.strftime('%Y-%m-%d %H:%M')}"
        },
        status=403
    )


    station_id = request.data.get("station_id")
    start_time = request.data.get("start_time")
    end_time = request.data.get("end_time")

    if not all([station_id, start_time, end_time]):
        return Response({"error": "station_id, start_time, end_time required"}, status=400)

    try:
        start = parse_iso_datetime(start_time)
        end = parse_iso_datetime(end_time)
    except Exception:
        return Response({"error": "Invalid datetime format"}, status=400)

    if end <= start:
        return Response({"error": "Invalid time range"}, status=400)

    try:
        station = ChargingStation.objects.get(id=station_id)
    except ChargingStation.DoesNotExist:
        return Response({"error": "Station not found"}, status=404)

    # Overlap check (active bookings that intersect requested window)
    overlapping = Booking.objects.filter(
        station=station,
        status="active",
        start_time__lt=end,
        end_time__gt=start,
    ).count()

    # If full -> add to waitlist
    if overlapping >= station.total_slots:
        existing = Waitlist.objects.filter(user=user, station=station).first()
        if existing:
            return Response({
                "message": "Station is full. You are already in the waitlist.",
                "position": existing.position,
            }, status=202)

        last = Waitlist.objects.filter(station=station).order_by("-position").first()
        next_pos = (last.position + 1) if last else 1

        from django.utils import timezone
        expires_at = timezone.now() + timedelta(minutes=30)
        Waitlist.objects.create(user=user, station=station, position=next_pos, expires_at=expires_at)

        # Send waitlist notification email asynchronously
        threading.Thread(
            target=send_waitlist_notification_email,
            args=(user, station, next_pos),
            daemon=True
        ).start()

        # Notify user via push
        tokens = DeviceToken.objects.filter(user=user)
        for t in tokens:
            send_push_notification(
                t.token,
                title="Added to Waitlist",
                body=f"Station {station.name} is full. Your waitlist position: {next_pos}",
            )

        # Re-order is not strictly necessary here (we appended at end),
        # but if any concurrent modifications happen we can call reorder.
        reorder_waitlist(station)

        return Response({
            "message": "Station is full. You have been added to the waitlist.",
            "position": next_pos,
        }, status=202)

    # Free slot -> create booking
    booking = Booking.objects.create(
        user=user,
        station=station,
        start_time=start,
        end_time=end,
        status="active",
    )
    # Decrease available slot
    station.available_slots = max(0, station.available_slots - 1)
    station.save()

    # Broadcast slot update to all WebSocket clients
    broadcast_slot_update(station.id)

    # Send booking confirmation email asynchronously (to avoid blocking)
    email_thread = threading.Thread(
        target=send_booking_confirmation_email,
        args=(user, booking, station)
    )
    email_thread.daemon = True
    email_thread.start()

    # Notify user via push
    tokens = DeviceToken.objects.filter(user=user)
    for t in tokens:
        send_push_notification(
            t.token,
            title="Booking Confirmed",
            body=f"You booked {station.name} from {start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}",
        )

    return Response({
        "message": "Booking created",
        "booking_id": booking.id,
    })


# ---------------------------------------------------------
# 4. SMART FILTERING (car-based)
# ---------------------------------------------------------
class SmartFilteredStations(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            user_car = UserCar.objects.get(user=request.user)
        except UserCar.DoesNotExist:
            return Response({"error": "Car not selected"}, status=400)

        car = user_car.car

        stations = ChargingStation.objects.filter(connector_type=car.connector_type)

        if getattr(car, "max_dc_power_kw", 0) >= 30:
            stations = stations.order_by(
                models.Case(
                    models.When(charger_type="DC", then=0),
                    models.When(charger_type="AC", then=1),
                    default=2,
                    output_field=models.IntegerField(),
                ),
                "-power_kw",
            )
        else:
            stations = stations.order_by(
                models.Case(
                    models.When(charger_type="AC", then=0),
                    models.When(charger_type="DC", then=1),
                    default=2,
                    output_field=models.IntegerField(),
                ),
                "-power_kw",
            )

        data = ChargingStationSerializer(stations, many=True).data
        return Response(data)


# ---------------------------------------------------------
# 5. TOPSIS – CUSTOM WEIGHTED MCDM
# ---------------------------------------------------------
@api_view(["POST"])
def topsis_custom(request):
    user_lat = request.data.get("lat")
    user_lng = request.data.get("lng")
    radius = float(request.data.get("radius", 15))
    top_n = int(request.data.get("top_n", 5))
    weights_dict = request.data.get("weights") or {}

    if user_lat is None or user_lng is None:
        return Response({"error": "lat and lng required"}, status=400)

    try:
        user_lat = float(user_lat)
        user_lng = float(user_lng)
    except (TypeError, ValueError):
        return Response({"error": "lat and lng must be numbers"}, status=400)

    if not isinstance(weights_dict, dict):
        return Response({"error": "weights must be a JSON object"}, status=400)

    required = [
        "available_slots",
        "power_kw",
        "waiting_time",
        "charging_time",
        "price_per_kwh",
        "distance",
    ]

    for k in required:
        if k not in weights_dict:
            return Response({"error": f"Weight '{k}' missing"}, status=400)

    weights = [float(weights_dict[k]) for k in required]

    nearby = []
    for st in ChargingStation.objects.all():
        dist = calculate_distance(user_lat, user_lng, st.latitude, st.longitude)
        if dist <= radius and st.available_slots > 0:
            st._distance = dist
            nearby.append(st)

    if not nearby:
        return Response({"error": "No available stations near your location"}, status=404)

    matrix, data = [], []

    for st in nearby:
        charging_time = st.power_kw / st.speed if getattr(st, "speed", 0) else 0

        row = [
            st.available_slots,
            st.power_kw,
            st.waiting_time,
            charging_time,
            st.price_per_kwh,
            st._distance,
        ]
        matrix.append(row)

        data.append(
            {
                "id": st.id,
                "name": st.name,
                "latitude": float(st.latitude),
                "longitude": float(st.longitude),
                "available_slots": st.available_slots,
                "power_kw": st.power_kw,
                "waiting_time": st.waiting_time,
                "charging_time": round(charging_time, 2),
                "price_per_kwh": st.price_per_kwh,
                "distance": round(st._distance, 2),
            }
        )

    impacts = ["+", "+", "-", "-", "-", "-"]
    scores = topsis(matrix, weights, impacts)

    for i, s in enumerate(scores):
        data[i]["score"] = round(s, 4)

    ranked = sorted(data, key=lambda x: x["score"], reverse=True)
    return Response(ranked[:top_n])


# ---------------------------------------------------------
# 6. BOOKING HISTORY
# ---------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_bookings(request):
    # Get all bookings, not just active ones, to show booking history
    print(f"DEBUG my_bookings: User requesting bookings: {request.user.username} (ID: {request.user.id})")
    qs = Booking.objects.filter(user=request.user).order_by("-created_at")
    print(f"DEBUG my_bookings: Found {qs.count()} bookings for user {request.user.username}")
    
    status_filter = request.GET.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)

    return Response(BookingSerializer(qs, many=True).data)


# ---------------------------------------------------------
# 6B. BOOKING DETAIL (single booking info)
# ---------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def booking_detail(request, booking_id):
    """Get details of a specific booking"""
    try:
        booking = Booking.objects.get(id=booking_id, user=request.user)
        return Response(BookingSerializer(booking).data)
    except Booking.DoesNotExist:
        return Response({"error": "Booking not found"}, status=404)


# ---------------------------------------------------------
# 6A. ACTIVE BOOKINGS (for sidebar)
# ---------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def active_bookings(request):
    """Get all active bookings for the current user"""
    now = timezone.now()
    # Get bookings that haven't ended yet
    qs = Booking.objects.filter(
        user=request.user,
        status="active",
        end_time__gt=now
    ).order_by("start_time").select_related('station')
    
    bookings_data = []
    for booking in qs:
        # Convert to local timezone before sending to frontend
        local_start = timezone.localtime(booking.start_time)
        local_end = timezone.localtime(booking.end_time)
        
        bookings_data.append({
            'id': booking.id,
            'station_name': booking.station.name,
            'station_id': booking.station.id,
            'start_time': local_start.isoformat(),
            'end_time': local_end.isoformat(),
            'charger_type': booking.station.charger_type,
            'power_kw': booking.station.power_kw,
            'address': booking.station.address,
            'station_latitude': booking.station.latitude,
            'station_longitude': booking.station.longitude,
        })
    
    return Response({'bookings': bookings_data, 'count': len(bookings_data)})


# ---------------------------------------------------------
# 7. CANCEL BOOKING (immediate promotion)
# ---------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def cancel_booking(request):
    booking_id = request.data.get("booking_id")
    if not booking_id:
        return Response({"error": "booking_id required"}, status=400)

    try:
        booking = Booking.objects.get(id=booking_id, user=request.user)
    except Booking.DoesNotExist:
        return Response({"error": "Booking not found"}, status=404)

    if booking.status != "active":
        return Response({"error": "Only active bookings can be cancelled"}, status=400)

    now_ = timezone.now()
    if booking.start_time <= now_:
        return Response({"error": "Booking already started"}, status=400)

    # Late cancel penalty (within 10 minutes of start)
    if booking.start_time - now_ <= timedelta(minutes=10):
        pen, _ = UserPenalty.objects.get_or_create(user=request.user)
        pen.late_cancel_count = getattr(pen, "late_cancel_count", 0) + 1
        pen.penalty_points = getattr(pen, "penalty_points", 0) + 1
        pen.save()

    station = booking.station
    booking.status = "cancelled"
    booking.save()

    # Increase available slots
    station.available_slots = min(station.total_slots, station.available_slots + 1)
    station.save()

    # Broadcast slot update to all WebSocket clients
    broadcast_slot_update(station.id)

    # notify user
    tokens = DeviceToken.objects.filter(user=request.user)
    for t in tokens:
        send_push_notification(
            t.token,
            title="Booking Cancelled",
            body=f"Your booking at {station.name} has been cancelled.",
        )

    # immediate promotion using centralized service
    promote_waitlist_for_station(station, notify=True)

    return Response({"message": "Booking cancelled"})


# ---------------------------------------------------------
# 7b. JOIN WAITLIST
# ---------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def join_waitlist(request):
    station_id = request.data.get("station_id")
    if not station_id:
        return Response({"error": "station_id required"}, status=400)

    try:
        station = ChargingStation.objects.get(id=station_id)
    except ChargingStation.DoesNotExist:
        return Response({"error": "Station not found"}, status=404)

    # Check if user already in waitlist
    if Waitlist.objects.filter(user=request.user, station=station).exists():
        return Response({"error": "Already in waitlist"}, status=400)

    # Get next position
    max_pos = Waitlist.objects.filter(station=station).aggregate(
        models.Max('position'))['position__max'] or 0
    new_position = max_pos + 1

    # Create waitlist entry
    from django.utils import timezone
    expires_at = timezone.now() + timedelta(minutes=30)
    entry = Waitlist.objects.create(
        user=request.user,
        station=station,
        position=new_position,
        expires_at=expires_at
    )

    # Send notification
    send_waitlist_notification_email(
        user_email=request.user.email,
        station_name=station.name,
        position=new_position
    )

    return Response({
        "message": "Added to waitlist",
        "position": new_position,
        "station": station.name
    })


# ---------------------------------------------------------
# 7c. GET WAITLIST POSITION
# ---------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_waitlist_position(request):
    station_id = request.GET.get("station_id")
    if not station_id:
        return Response({"error": "station_id required"}, status=400)

    try:
        station = ChargingStation.objects.get(id=station_id)
    except ChargingStation.DoesNotExist:
        return Response({"error": "Station not found"}, status=404)

    info = get_waitlist_info(request.user, station)
    if not info:
        return Response({"message": "User not in waitlist"}, status=404)

    return Response({
        "station_id": station.id,
        "position": info["position"],
        "estimated_wait_minutes": info["estimated_wait_minutes"]
    })


# ---------------------------------------------------------
# 7d. LEAVE WAITLIST
# ---------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def leave_waitlist(request):
    station_id = request.data.get("station_id")
    if not station_id:
        return Response({"error": "station_id required"}, status=400)

    try:
        station = ChargingStation.objects.get(id=station_id)
    except ChargingStation.DoesNotExist:
        return Response({"error": "Station not found"}, status=404)

    try:
        entry = Waitlist.objects.get(user=request.user, station=station)
        entry.delete()
        reorder_waitlist(station)
        return Response({"message": "Removed from waitlist"})
    except Waitlist.DoesNotExist:
        return Response({"error": "Not in waitlist"}, status=404)


# ---------------------------------------------------------
# 8. GET / UPDATE PROFILE
# ---------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_profile(request):
    serializer = UserProfileSerializer(request.user)
    return Response(serializer.data)


@api_view(["PUT", "PATCH"])
@permission_classes([IsAuthenticated])
def update_profile(request):
    user = request.user
    data = request.data

    user.first_name = data.get("first_name", user.first_name)
    user.last_name = data.get("last_name", user.last_name)
    user.email = data.get("email", user.email)
    user.save()

    return Response({"message": "Profile updated"})


# ---------------------------------------------------------
# 9. FAVOURITES
# ---------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def toggle_favourite(request):
    station_id = request.data.get("station_id")
    if not station_id:
        return Response({"error": "station_id required"}, status=400)

    try:
        station = ChargingStation.objects.get(id=station_id)
    except ChargingStation.DoesNotExist:
        return Response({"error": "Station not found"}, status=404)

    fav, created = FavouriteStation.objects.get_or_create(
        user=request.user,
        station=station,
    )

    if not created:
        fav.delete()
        return Response({"message": "Removed from favourites", "action": "removed"})

    return Response({"message": "Added to favourites", "action": "added"})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_favourites(request):
    favs = FavouriteStation.objects.filter(user=request.user).order_by("-added_at")
    serializer = FavouriteStationSerializer(favs, many=True)
    return Response(serializer.data)


# ---------------------------------------------------------
# 10. MAP MARKERS / ROUTE / BEST STOPS / SEARCH
# ---------------------------------------------------------
@api_view(["GET"])
def map_nearby_stations(request):
    try:
        user_lat = float(request.GET.get("lat"))
        user_lng = float(request.GET.get("lng"))
    except (TypeError, ValueError):
        return Response({"error": "lat and lng are required"}, status=400)

    radius = float(request.GET.get("radius", 10))  # km

    station_tuples = _get_nearby_station_tuples(user_lat, user_lng, radius)

    result = [
        {
            "id": st.id,
            "name": st.name,
            "latitude": st.latitude,
            "longitude": st.longitude,
            "distance_km": round(dist, 2),
        }
        for st, dist in station_tuples
    ]

    return Response(result)


def min_distance_to_route(station, route_points):
    """Calculate minimum distance from station to the route path (perpendicular distance to route segments)"""
    if len(route_points) < 2:
        # If only one point, return distance to that point
        return calculate_distance(station.latitude, station.longitude, route_points[0]["lat"], route_points[0]["lng"])
    
    min_dist = float('inf')
    
    # Check perpendicular distance to each route segment
    for i in range(len(route_points) - 1):
        p1 = route_points[i]
        p2 = route_points[i + 1]
        
        # Calculate perpendicular distance from station to line segment
        # Using the point-to-line-segment distance formula
        lat1, lng1 = p1["lat"], p1["lng"]
        lat2, lng2 = p2["lat"], p2["lng"]
        lat0, lng0 = station.latitude, station.longitude
        
        # Convert to approximate meters (rough approximation)
        # 1 degree lat ≈ 111 km, 1 degree lng ≈ 111 * cos(lat) km
        x1, y1 = lng1 * 111000 * np.cos(np.radians(lat1)), lat1 * 111000
        x2, y2 = lng2 * 111000 * np.cos(np.radians(lat2)), lat2 * 111000
        x0, y0 = lng0 * 111000 * np.cos(np.radians(lat0)), lat0 * 111000
        
        # Perpendicular distance formula
        numerator = abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1)
        denominator = np.sqrt((y2 - y1) ** 2 + (x2 - x1) ** 2)
        
        if denominator > 0:
            dist_meters = numerator / denominator
        else:
            dist_meters = calculate_distance(lat0, lng0, lat1, lng1) * 1000
        
        # Check if perpendicular point is within segment bounds
        if denominator > 0:
            t = ((x0 - x1) * (x2 - x1) + (y0 - y1) * (y2 - y1)) / (denominator ** 2)
            if 0 <= t <= 1:
                # Perpendicular point is within segment
                min_dist = min(min_dist, dist_meters / 1000)  # Convert back to km
            else:
                # Perpendicular point is outside segment, use distance to nearest endpoint
                dist_to_p1 = calculate_distance(lat0, lng0, lat1, lng1)
                dist_to_p2 = calculate_distance(lat0, lng0, lat2, lng2)
                min_dist = min(min_dist, dist_to_p1, dist_to_p2)
        else:
            dist_to_p1 = calculate_distance(lat0, lng0, lat1, lng1)
            min_dist = min(min_dist, dist_to_p1)
    
    return min_dist if min_dist != float('inf') else float('inf')


@api_view(["POST"])
def stations_along_route(request):
    route = request.data.get("route")
    radius = float(request.data.get("radius", 2))  # Reduced from 5 to 2 km for better accuracy
    connector = request.data.get("connector")

    if not route:
        return Response({"error": "Route points required"}, status=400)

    qs = ChargingStation.objects.all()
    if connector:
        qs = qs.filter(connector_type=connector)

    result = []
    for st in qs:
        dist = min_distance_to_route(st, route)
        # Only include stations that are actually near the route
        if dist <= radius:
            result.append({
                "id": st.id,
                "name": st.name,
                "lat": st.latitude,
                "lng": st.longitude,
                "distance_from_route": round(dist, 2),
                "available_slots": st.available_slots,
                "power_kw": st.power_kw,
                "price_per_kwh": st.price_per_kwh,
                "waiting_time": st.waiting_time,
            })

    return Response(result)


@api_view(["POST"])
def best_charge_stops(request):
    route = request.data.get("route")
    radius = float(request.data.get("radius", 2))  # Reduced default from 5 to 2 km
    max_stops = int(request.data.get("max_stops", 3))

    if not route:
        return Response({"error": "Route points required"}, status=400)

    candidates = []
    for st in ChargingStation.objects.all():
        # Calculate actual distance from station to route
        dist = min_distance_to_route(st, route)
        
        # Only include stations actually near the route (within radius)
        if dist <= radius:
            score = (st.available_slots * 2) + (st.power_kw * 1.5) - (dist * 0.5)
            candidates.append({
                "id": st.id,
                "name": st.name,
                "distance_from_route": round(dist, 2),
                "available_slots": st.available_slots,
                "power_kw": st.power_kw,
                "score": round(score, 2)
            })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return Response(candidates[:max_stops])


@api_view(["GET"])
def station_search(request):
    from .topsis import topsis
    
    q = request.GET.get("q", "").strip()
    if not q:
        # Return ALL stations if no search query
        stations = ChargingStation.objects.all()
    else:
        stations = ChargingStation.objects.filter(
            models.Q(name__icontains=q) | 
            models.Q(address__icontains=q)
        )
    
    # Prepare data for TOPSIS scoring
    station_list = list(stations)
    if len(station_list) > 0:
        # TOPSIS criteria: [power_kw, availability_percentage, speed, -price_per_kwh, -waiting_time]
        # Positive impacts: power, availability, speed
        # Negative impacts: price, waiting time
        matrix = []
        for st in station_list:
            availability_pct = (st.available_slots / st.total_slots * 100) if st.total_slots > 0 else 0
            matrix.append([
                st.power_kw,  # Higher is better
                availability_pct,  # Higher is better
                st.speed,  # Higher is better
                st.price_per_kwh,  # Lower is better (will use negative impact)
                st.waiting_time,  # Lower is better (will use negative impact)
            ])
        
        weights = [0.25, 0.25, 0.15, 0.2, 0.15]  # Weights for each criterion
        impacts = ['+', '+', '+', '-', '-']  # + for benefit, - for cost
        
        try:
            topsis_scores = topsis(matrix, weights, impacts)
        except:
            topsis_scores = [0.5] * len(station_list)
    else:
        topsis_scores = []
    
    result = []
    for idx, st in enumerate(station_list):
        availability_pct = (st.available_slots / st.total_slots * 100) if st.total_slots > 0 else 0
        result.append({
            "id": st.id,
            "name": st.name,
            "address": st.address,
            "latitude": st.latitude,
            "longitude": st.longitude,
            "charger_type": st.charger_type,
            "connector_type": st.connector_type,
            "power_kw": st.power_kw,
            "total_slots": st.total_slots,
            "available_slots": st.available_slots,
            "availability_percentage": round(availability_pct, 1),
            "price_per_kwh": st.price_per_kwh,
            "waiting_time": st.waiting_time,
            "speed": st.speed,
            "status": st.status,
            "topsis_score": round(topsis_scores[idx], 3) if idx < len(topsis_scores) else 0.5,
        })
    return Response(result)


# ---------------------------------------------------------
# 11. DEVICE TOKEN (FCM)
# ---------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def save_device_token(request):
    token = request.data.get("token")
    if not token:
        return Response({"error": "token required"}, status=400)

    # store or update token for this user
    DeviceToken.objects.update_or_create(user=request.user, defaults={"token": token})

    return Response({"message": "Token saved"})


# ---------------------------------------------------------
# 12. P2P APIs
# ---------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def p2p_create_charger(request):
    data = request.data.copy()
    serializer = PeerChargerSerializer(data=data)
    if serializer.is_valid():
        serializer.save(owner=request.user)
        return Response(serializer.data, status=201)
    return Response(serializer.errors, status=400)


@api_view(["GET"])
def p2p_nearby_chargers(request):
    try:
        lat = float(request.GET.get("lat"))
        lng = float(request.GET.get("lng"))
    except (TypeError, ValueError):
        return Response({"error": "lat and lng required"}, status=400)

    radius = float(request.GET.get("radius", 10))
    connector = request.GET.get("connector")

    chargers = PeerCharger.objects.filter(is_active=True)
    if connector:
        chargers = chargers.filter(connector_type=connector)

    result = []
    for ch in chargers:
        dist = calculate_distance(lat, lng, ch.latitude, ch.longitude)
        if dist <= radius:
            result.append({
                "id": ch.id,
                "name": ch.name,
                "owner": ch.owner.username,
                "latitude": ch.latitude,
                "longitude": ch.longitude,
                "connector_type": ch.connector_type,
                "power_kw": ch.power_kw,
                "price_per_kwh": ch.price_per_kwh,
                "distance_km": round(dist, 2),
            })

    result.sort(key=lambda x: x["distance_km"])
    return Response(result)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def p2p_create_booking(request):
    charger_id = request.data.get("charger_id")
    start_time = request.data.get("start_time")
    end_time = request.data.get("end_time")

    if not all([charger_id, start_time, end_time]):
        return Response({"error": "charger_id, start_time, end_time required"}, status=400)

    try:
        charger = PeerCharger.objects.get(id=charger_id, is_active=True)
    except PeerCharger.DoesNotExist:
        return Response({"error": "Charger not found"}, status=404)

    try:
        start = parse_iso_datetime(start_time)
        end = parse_iso_datetime(end_time)
    except Exception:
        return Response({"error": "Invalid datetime format"}, status=400)

    if end <= start:
        return Response({"error": "Invalid time range"}, status=400)

    overlap = PeerBooking.objects.filter(
        charger=charger,
        status__in=["pending", "approved"],
        start_time__lt=end,
        end_time__gt=start,
    ).exists()

    if overlap:
        return Response({"error": "Slot not available for this charger"}, status=409)

    booking = PeerBooking.objects.create(
        renter=request.user,
        charger=charger,
        start_time=start,
        end_time=end,
        status="pending",
    )

    owner_tokens = DeviceToken.objects.filter(user=charger.owner)
    for t in owner_tokens:
        send_push_notification(
            t.token,
            title="New P2P booking request",
            body=f"{request.user.username} requested your charger '{charger.name}'",
        )

    return Response({"message": "Booking created, waiting for owner approval", "booking_id": booking.id})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def p2p_approve(request):
    booking_id = request.data.get("booking_id")
    action = request.data.get("action")  # "approve" or "reject"

    if not booking_id or action not in ["approve", "reject"]:
        return Response({"error": "booking_id and valid action required"}, status=400)

    try:
        booking = PeerBooking.objects.get(id=booking_id)
    except PeerBooking.DoesNotExist:
        return Response({"error": "Booking not found"}, status=404)

    if booking.charger.owner != request.user:
        return Response({"error": "Not authorized"}, status=403)

    if booking.status != "pending":
        return Response({"error": "Booking already processed"}, status=400)

    booking.status = "approved" if action == "approve" else "rejected"
    booking.save()

    renter_tokens = DeviceToken.objects.filter(user=booking.renter)
    status_text = "approved" if action == "approve" else "rejected"
    for t in renter_tokens:
        send_push_notification(t.token, title=f"P2P booking {status_text}", body=f"Your booking for {booking.charger.name} was {status_text}.")

    return Response({"message": f"Booking {status_text}."})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def p2p_my_bookings(request):
    qs = PeerBooking.objects.filter(renter=request.user).order_by("-start_time")
    serializer = PeerBookingSerializer(qs, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def p2p_owner_requests(request):
    qs = PeerBooking.objects.filter(charger__owner=request.user, status="pending").order_by("-created_at")
    serializer = PeerBookingSerializer(qs, many=True)
    return Response(serializer.data)


# ---------------------------------------------------------
# 13. Ratings & station info
# ---------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def rate_station(request):
    station_id = request.data.get("station_id")
    rating = request.data.get("rating")
    review = request.data.get("review", "")  # Optional review text

    if not station_id or rating is None:
        return Response({"error": "station_id and rating required"}, status=400)

    try:
        rating_val = int(rating)
    except (TypeError, ValueError):
        return Response({"error": "rating must be an integer 1-5"}, status=400)

    if rating_val < 1 or rating_val > 5:
        return Response({"error": "rating must be 1-5"}, status=400)

    try:
        station = ChargingStation.objects.get(id=station_id)
    except ChargingStation.DoesNotExist:
        return Response({"error": "Station not found"}, status=404)

    obj, created = StationRating.objects.update_or_create(
        user=request.user,
        station=station,
        defaults={"rating": rating_val, "review": review},
    )

    return Response({
        "message": "Rating saved" if created else "Rating updated",
        "rating": rating_val,
        "review": review
    })


from django.db.models import Avg, Count


@api_view(["GET"])
def station_rating(request, station_id):
    avg = StationRating.objects.filter(station_id=station_id).aggregate(avg=Avg("rating"))["avg"]
    count = StationRating.objects.filter(station_id=station_id).count()
    
    # Get recent reviews with user info
    ratings = StationRating.objects.filter(station_id=station_id).select_related('user')[:10]
    reviews_data = [{
        "username": r.user.username,
        "rating": r.rating,
        "review": r.review,
        "helpful_count": r.helpful_count,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M"),
    } for r in ratings if r.review]  # Only include reviews with text
    
    return Response({
        "station_id": station_id,
        "avg_rating": round(avg or 0, 2),
        "total_ratings": count,
        "reviews": reviews_data
    })


@api_view(["GET"])
def station_full_info(request):
    try:
        user_lat = float(request.GET.get("lat"))
        user_lng = float(request.GET.get("lng"))
    except (TypeError, ValueError):
        return Response({"error": "lat and lng required"}, status=400)

    try:
        radius = float(request.GET.get("radius", 20))
    except ValueError:
        return Response({"error": "radius must be a number"}, status=400)

    connector = request.GET.get("connector")
    charger_type = request.GET.get("charger_type")
    min_power = request.GET.get("min_power")
    max_price = request.GET.get("max_price")
    min_rating = request.GET.get("min_rating")
    sort_by = request.GET.get("sort", "distance")

    qs = ChargingStation.objects.all()
    if connector:
        qs = qs.filter(connector_type__iexact=connector)
    if charger_type:
        qs = qs.filter(charger_type__iexact=charger_type)
    if min_power:
        qs = qs.filter(power_kw__gte=float(min_power))
    if max_price:
        qs = qs.filter(price_per_kwh__lte=float(max_price))

    result = []
    for st in qs:
        dist = calculate_distance(user_lat, user_lng, st.latitude, st.longitude)
        if dist <= radius:
            rating = StationRating.objects.filter(station=st).aggregate(avg=Avg("rating"))["avg"]
            rating = round(rating or 0, 2)

            if min_rating and rating < float(min_rating):
                continue

            result.append({
                "id": st.id,
                "name": st.name,
                "latitude": st.latitude,
                "longitude": st.longitude,
                "connector_type": st.connector_type,
                "charger_type": st.charger_type,
                "power_kw": st.power_kw,
                "price_per_kwh": st.price_per_kwh,
                "available_slots": st.available_slots,
                "distance_km": round(dist, 2),
                "avg_rating": rating,
            })

    if sort_by == "distance":
        result.sort(key=lambda x: x["distance_km"])
    elif sort_by == "rating":
        result.sort(key=lambda x: x["avg_rating"], reverse=True)
    elif sort_by == "power":
        result.sort(key=lambda x: x["power_kw"], reverse=True)
    elif sort_by == "slots":
        result.sort(key=lambda x: x["available_slots"], reverse=True)
    elif sort_by == "price":
        result.sort(key=lambda x: x["price_per_kwh"])

    return Response(result)


# =====================================================
# STATION DETAIL VIEW
# =====================================================
@api_view(['GET'])
def station_detail(request, station_id):
    """
    Get detailed information about a specific station including
    images, facilities, ratings, contact information, etc.
    """
    try:
        station = ChargingStation.objects.get(id=station_id)
    except ChargingStation.DoesNotExist:
        return Response({"error": "Station not found"}, status=404)
    
    # Serialize with all fields
    serializer = ChargingStationSerializer(station)
    data = serializer.data
    
    # Add user's distance if lat/lng provided
    user_lat = request.GET.get("user_lat")
    user_lng = request.GET.get("user_lng")
    if user_lat and user_lng:
        try:
            user_lat = float(user_lat)
            user_lng = float(user_lng)
            dist = calculate_distance(user_lat, user_lng, station.latitude, station.longitude)
            data['distance_km'] = round(dist, 2)
        except (TypeError, ValueError):
            pass
    
    # Add ratings count
    ratings_data = StationRating.objects.filter(station=station).aggregate(
        count=models.Count('id'),
        avg=Avg('rating')
    )
    data['ratings_count'] = ratings_data['count'] or 0
    
    # Add recent reviews (last 5)
    recent_ratings = StationRating.objects.filter(station=station).order_by('-created_at')[:5]
    data['recent_reviews'] = [
        {
            'rating': r.rating,
            'review': 'Great station!',  # Default review text
            'username': r.user.username,
            'created_at': timezone.localtime(r.created_at).strftime('%Y-%m-%d %H:%M')
        }
        for r in recent_ratings
    ]

    # Add favourite status when user is authenticated
    try:
        data['is_favorited'] = False
        if request.user and request.user.is_authenticated:
            data['is_favorited'] = FavouriteStation.objects.filter(user=request.user, station=station).exists()
    except Exception:
        data['is_favorited'] = False
    
    return Response(data)


# ---------------------------------------------------------
# RECENTLY VIEWED STATIONS
# ---------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def track_station_view(request):
    """Track when a user views a station detail page"""
    from .models import RecentlyViewedStation
    
    station_id = request.data.get("station_id")
    if not station_id:
        return Response({"error": "station_id required"}, status=400)
    
    try:
        station = ChargingStation.objects.get(id=station_id)
    except ChargingStation.DoesNotExist:
        return Response({"error": "Station not found"}, status=404)
    
    # Update or create recent view
    RecentlyViewedStation.objects.update_or_create(
        user=request.user,
        station=station,
        defaults={"viewed_at": timezone.now()}
    )
    
    # Keep only last 10 viewed stations per user
    user_views = RecentlyViewedStation.objects.filter(user=request.user).order_by('-viewed_at')
    if user_views.count() > 10:
        to_delete = user_views[10:]
        RecentlyViewedStation.objects.filter(id__in=[v.id for v in to_delete]).delete()
    
    return Response({"message": "Station view tracked"})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def recently_viewed_stations(request):
    """Get user's recently viewed stations"""
    from .models import RecentlyViewedStation
    
    recent_views = RecentlyViewedStation.objects.filter(
        user=request.user
    ).select_related('station').order_by('-viewed_at')[:10]
    
    stations_data = []
    for view in recent_views:
        station = view.station
        stations_data.append({
            'id': station.id,
            'name': station.name,
            'address': station.address,
            'charger_type': station.charger_type,
            'power_kw': station.power_kw,
            'price_per_kwh': station.price_per_kwh,
            'available_slots': station.available_slots,
            'total_slots': station.total_slots,
            'latitude': station.latitude,
            'longitude': station.longitude,
            'viewed_at': timezone.localtime(view.viewed_at).isoformat(),
        })
    
    return Response({"recently_viewed": stations_data})


# ---------------------------------------------------------
# ADMIN DASHBOARD VIEWS
# ---------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_dashboard_stats(request):
    """Get admin dashboard statistics"""
    from django.contrib.auth.models import User
    from django.db.models import Sum, Avg, Count, Q
    from django.db.models.functions import TruncDate
    
    # Check if user is admin/staff
    if not request.user.is_staff:
        return Response({"error": "Admin access required"}, status=403)
    
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    seven_days_ago = now - timedelta(days=7)
    
    # Overall Statistics
    total_users = User.objects.count()
    total_stations = ChargingStation.objects.count()
    total_bookings = Booking.objects.count()
    active_bookings = Booking.objects.filter(status="active").count()
    
    # Revenue calculations (estimated)
    completed_bookings = Booking.objects.filter(status="completed")
    estimated_revenue = 0
    for booking in completed_bookings:
        duration_hours = (booking.end_time - booking.start_time).total_seconds() / 3600
        energy_kwh = duration_hours * 30
        cost = energy_kwh * booking.station.price_per_kwh
        estimated_revenue += cost
    
    # Recent activity (last 30 days)
    new_users_30d = User.objects.filter(date_joined__gte=thirty_days_ago).count()
    bookings_30d = Booking.objects.filter(created_at__gte=thirty_days_ago).count()
    bookings_7d = Booking.objects.filter(created_at__gte=seven_days_ago).count()
    
    # Station utilization
    total_capacity = ChargingStation.objects.aggregate(Sum('total_slots'))['total_slots__sum'] or 0
    total_available = ChargingStation.objects.aggregate(Sum('available_slots'))['available_slots__sum'] or 0
    utilization_rate = ((total_capacity - total_available) / total_capacity * 100) if total_capacity > 0 else 0
    
    # Top stations by bookings
    top_stations = Booking.objects.values(
        'station__name', 'station__id'
    ).annotate(
        booking_count=Count('id')
    ).order_by('-booking_count')[:5]
    
    # Daily bookings for last 7 days
    daily_bookings = Booking.objects.filter(
        created_at__gte=seven_days_ago
    ).annotate(
        date=TruncDate('created_at')
    ).values('date').annotate(
        count=Count('id')
    ).order_by('date')
    
    # User activity
    active_users_7d = Booking.objects.filter(
        created_at__gte=seven_days_ago
    ).values('user').distinct().count()
    
    return Response({
        'overview': {
            'total_users': total_users,
            'total_stations': total_stations,
            'total_bookings': total_bookings,
            'active_bookings': active_bookings,
            'estimated_revenue': round(estimated_revenue, 2),
            'utilization_rate': round(utilization_rate, 2),
        },
        'recent_activity': {
            'new_users_30d': new_users_30d,
            'bookings_30d': bookings_30d,
            'bookings_7d': bookings_7d,
            'active_users_7d': active_users_7d,
        },
        'top_stations': list(top_stations),
        'daily_bookings': list(daily_bookings),
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_revenue_analytics(request):
    """Detailed revenue analytics for admin"""
    if not request.user.is_staff:
        return Response({"error": "Admin access required"}, status=403)
    
    from django.db.models.functions import TruncDate, TruncMonth
    
    days = int(request.GET.get('days', 30))
    start_date = timezone.now() - timedelta(days=days)
    
    bookings = Booking.objects.filter(
        created_at__gte=start_date,
        status__in=['completed', 'active']
    ).select_related('station')
    
    daily_revenue = {}
    monthly_revenue = {}
    station_revenue = {}
    
    for booking in bookings:
        duration_hours = (booking.end_time - booking.start_time).total_seconds() / 3600
        energy_kwh = duration_hours * 30
        revenue = energy_kwh * booking.station.price_per_kwh
        
        date_key = booking.created_at.date().isoformat()
        daily_revenue[date_key] = daily_revenue.get(date_key, 0) + revenue
        
        month_key = booking.created_at.strftime('%Y-%m')
        monthly_revenue[month_key] = monthly_revenue.get(month_key, 0) + revenue
        
        station_key = booking.station.name
        if station_key not in station_revenue:
            station_revenue[station_key] = {
                'station_id': booking.station.id,
                'revenue': 0,
                'booking_count': 0
            }
        station_revenue[station_key]['revenue'] += revenue
        station_revenue[station_key]['booking_count'] += 1
    
    top_revenue_stations = sorted(
        station_revenue.items(),
        key=lambda x: x[1]['revenue'],
        reverse=True
    )[:10]
    
    total_revenue = sum(daily_revenue.values())
    avg_daily_revenue = total_revenue / days if days > 0 else 0
    
    return Response({
        'summary': {
            'total_revenue': round(total_revenue, 2),
            'avg_daily_revenue': round(avg_daily_revenue, 2),
            'total_bookings': bookings.count(),
        },
        'daily_revenue': [
            {'date': k, 'revenue': round(v, 2)} 
            for k, v in sorted(daily_revenue.items())
        ],
        'monthly_revenue': [
            {'month': k, 'revenue': round(v, 2)} 
            for k, v in sorted(monthly_revenue.items())
        ],
        'top_stations': [
            {
                'name': k,
                'station_id': v['station_id'],
                'revenue': round(v['revenue'], 2),
                'bookings': v['booking_count']
            }
            for k, v in top_revenue_stations
        ],
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_user_management(request):
    """User management data for admin"""
    if not request.user.is_staff:
        return Response({"error": "Admin access required"}, status=403)
    
    from django.contrib.auth.models import User
    from django.db.models import Count, Sum
    
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 20))
    search = request.GET.get('search', '')
    
    users = User.objects.all()
    if search:
        users = users.filter(
            Q(username__icontains=search) | 
            Q(email__icontains=search)
        )
    
    users = users.annotate(
        total_bookings=Count('booking'),
        active_bookings=Count('booking', filter=Q(booking__status='active'))
    ).order_by('-date_joined')
    
    total_users = users.count()
    start = (page - 1) * per_page
    end = start + per_page
    users_page = users[start:end]
    
    users_data = []
    for user in users_page:
        try:
            user_car = UserCar.objects.get(user=user)
            car_info = user_car.car.name if user_car.car else "No car selected"
        except UserCar.DoesNotExist:
            car_info = "No car selected"
        
        try:
            penalty = UserPenalty.objects.get(user=user)
            penalty_points = penalty.penalty_points
        except UserPenalty.DoesNotExist:
            penalty_points = 0
        
        users_data.append({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'date_joined': user.date_joined.isoformat(),
            'is_active': user.is_active,
            'is_staff': user.is_staff,
            'total_bookings': user.total_bookings,
            'active_bookings': user.active_bookings,
            'car': car_info,
            'penalty_points': penalty_points,
        })
    
    return Response({
        'users': users_data,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total_users,
            'pages': (total_users + per_page - 1) // per_page,
        }
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_booking_analytics(request):
    """Booking analytics for admin"""
    if not request.user.is_staff:
        return Response({"error": "Admin access required"}, status=403)
    
    from django.db.models.functions import TruncDate, TruncHour
    
    days = int(request.GET.get('days', 30))
    start_date = timezone.now() - timedelta(days=days)
    
    status_distribution = Booking.objects.filter(
        created_at__gte=start_date
    ).values('status').annotate(count=Count('id'))
    
    bookings_with_duration = Booking.objects.filter(
        created_at__gte=start_date
    ).exclude(start_time=None).exclude(end_time=None)
    
    durations = []
    for booking in bookings_with_duration:
        duration = (booking.end_time - booking.start_time).total_seconds() / 3600
        durations.append(duration)
    
    avg_duration = sum(durations) / len(durations) if durations else 0
    
    total_bookings = Booking.objects.filter(created_at__gte=start_date).count()
    cancelled_bookings = Booking.objects.filter(
        created_at__gte=start_date,
        status='cancelled'
    ).count()
    cancellation_rate = (cancelled_bookings / total_bookings * 100) if total_bookings > 0 else 0
    
    # Peak usage times - calculate in Python for SQLite compatibility
    hour_counts = {}
    for booking in Booking.objects.filter(created_at__gte=start_date).exclude(start_time=None):
        hour = booking.start_time.hour
        hour_counts[hour] = hour_counts.get(hour, 0) + 1
    
    # Get top 5 peak hours
    sorted_hours = sorted(hour_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    peak_hours = [{'hour': h, 'count': c} for h, c in sorted_hours]
    
    return Response({
        'summary': {
            'total_bookings': total_bookings,
            'cancelled_bookings': cancelled_bookings,
            'cancellation_rate': round(cancellation_rate, 2),
            'avg_duration_hours': round(avg_duration, 2),
        },
        'status_distribution': list(status_distribution),
        'peak_hours': peak_hours,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_station_management(request):
    """Station management data for admin"""
    if not request.user.is_staff:
        return Response({"error": "Admin access required"}, status=403)
    
    from django.db.models import Count, Avg
    
    stations = ChargingStation.objects.annotate(
        total_bookings=Count('booking'),
        active_bookings=Count('booking', filter=Q(booking__status='active')),
        avg_rating=Avg('ratings__rating')
    ).order_by('-total_bookings')
    
    stations_data = []
    for station in stations:
        utilization = ((station.total_slots - station.available_slots) / station.total_slots * 100) if station.total_slots > 0 else 0
        
        stations_data.append({
            'id': station.id,
            'name': station.name,
            'address': station.address,
            'charger_type': station.charger_type,
            'power_kw': station.power_kw,
            'price_per_kwh': station.price_per_kwh,
            'total_slots': station.total_slots,
            'available_slots': station.available_slots,
            'utilization_rate': round(utilization, 2),
            'status': station.status,
            'total_bookings': station.total_bookings,
            'active_bookings': station.active_bookings,
            'avg_rating': round(station.avg_rating, 2) if station.avg_rating else 0,
            'verified': station.verified,
            'last_updated': station.last_updated.isoformat(),
        })
    
    return Response({
        'stations': stations_data,
        'total_count': len(stations_data),
    })


# ============================================================
# CROWD-SOURCED STATION REPORTS
# ============================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def submit_report(request):
    station_id  = request.data.get("station_id")
    report_type = request.data.get("report_type")
    note        = request.data.get("note", "")[:200]

    VALID = ['broken', 'queue', 'closed', 'offline', 'clean']
    if not station_id or report_type not in VALID:
        return Response({"error": "station_id and valid report_type required"}, status=400)

    try:
        station = ChargingStation.objects.get(id=station_id)
    except ChargingStation.DoesNotExist:
        return Response({"error": "Station not found"}, status=404)

    expires_at = timezone.now() + timedelta(hours=2)
    report = StationReport.objects.create(
        station=station, user=request.user,
        report_type=report_type, note=note, expires_at=expires_at,
    )
    return Response({"message": "Report submitted", "report_id": report.id,
                     "expires_at": expires_at.isoformat()})


@api_view(["GET"])
def get_station_reports(request, station_id):
    now = timezone.now()
    reports = StationReport.objects.filter(station_id=station_id, expires_at__gt=now)
    return Response([{
        "id": r.id,
        "report_type": r.report_type,
        "note": r.note,
        "user": r.user.username if r.user else "Anonymous",
        "created_at": r.created_at.isoformat(),
        "expires_at": r.expires_at.isoformat(),
        "upvotes": r.upvotes,
        "minutes_ago": max(0, int((now - r.created_at).total_seconds() / 60)),
    } for r in reports])


@api_view(["GET"])
def get_all_active_reports(request):
    now = timezone.now()
    reports = StationReport.objects.filter(expires_at__gt=now).select_related('station')
    return Response([{
        "id": r.id,
        "station_id": r.station.id,
        "station_name": r.station.name,
        "latitude": r.station.latitude,
        "longitude": r.station.longitude,
        "report_type": r.report_type,
        "note": r.note,
        "user": r.user.username if r.user else "Anonymous",
        "created_at": r.created_at.isoformat(),
        "expires_at": r.expires_at.isoformat(),
        "upvotes": r.upvotes,
        "minutes_ago": max(0, int((now - r.created_at).total_seconds() / 60)),
    } for r in reports])


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def upvote_report(request):
    report_id = request.data.get("report_id")
    try:
        r = StationReport.objects.get(id=report_id, expires_at__gt=timezone.now())
        r.upvotes += 1
        r.save(update_fields=['upvotes'])
        return Response({"upvotes": r.upvotes})
    except StationReport.DoesNotExist:
        return Response({"error": "Report not found or expired"}, status=404)


import requests as _req_lib

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def geocode_search(request):
    q = request.query_params.get('q', '').strip()
    if not q:
        return Response([], status=200)
    try:
        resp = _req_lib.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': q, 'format': 'json', 'limit': '7', 'countrycodes': 'in'},
            headers={'User-Agent': 'ObturoEV-Backend/1.0'},
            timeout=10,
        )
        results = resp.json()
        data = [
            {
                'name': r.get('name', ''),
                'display_name': r.get('display_name', ''),
                'lat': r.get('lat'),
                'lon': r.get('lon'),
                'type': r.get('type', ''),
            }
            for r in results
        ]
        return Response(data)
    except Exception as e:
        return Response({'error': str(e)}, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def osrm_proxy(request):
    """Proxy OSRM routing requests so the device doesn't need external internet."""
    origin_lat = request.query_params.get('origin_lat')
    origin_lng = request.query_params.get('origin_lng')
    dest_lat   = request.query_params.get('dest_lat')
    dest_lng   = request.query_params.get('dest_lng')
    if not all([origin_lat, origin_lng, dest_lat, dest_lng]):
        return Response({'error': 'Missing parameters'}, status=400)
    try:
        url = (
            f'https://router.project-osrm.org/route/v1/driving/'
            f'{origin_lng},{origin_lat};{dest_lng},{dest_lat}'
            f'?overview=full&geometries=geojson'
        )
        resp = _req_lib.get(url, timeout=20)
        return Response(resp.json())
    except Exception as e:
        return Response({'error': str(e)}, status=500)


from django.http import HttpResponse
from rest_framework.permissions import AllowAny

@api_view(['GET'])
@permission_classes([AllowAny])
def tile_proxy(request, z, x, y):
    """Proxy map tiles from CartoCDN so the device doesn't need external internet."""
    try:
        url = f'https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png'
        resp = _req_lib.get(
            url,
            headers={'User-Agent': 'ObturoEV-Backend/1.0'},
            timeout=10,
        )
        return HttpResponse(
            resp.content,
            content_type=resp.headers.get('Content-Type', 'image/png'),
        )
    except Exception:
        return HttpResponse(status=503)
