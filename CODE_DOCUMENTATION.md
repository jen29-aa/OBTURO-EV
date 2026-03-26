# Obturo Backend — Complete Code Documentation

---

## Overview

Obturo is a Django REST Framework backend for an EV (Electric Vehicle) charging station platform. It is organised into three Django apps:

| App | Purpose |
|-----|---------|
| `accounts` | User registration, login (JWT), car profiles, device tokens |
| `stations` | Charging stations, bookings, waitlist, TOPSIS ranking, P2P sharing, ratings, reports |
| `web` | HTML template front-end served by Django views |

The project uses **Django Channels** for real-time WebSocket slot updates, **Firebase Cloud Messaging** for push notifications, **APScheduler** for background cron jobs, and the **TOPSIS** multi-criteria decision algorithm for intelligent station ranking.

---

## Step 1 — accounts/models.py

This file defines the data that belongs to a user's profile.

```python
from django.db import models
from django.contrib.auth.models import User

# Car catalogue — every supported EV model
class Car(models.Model):
    name = models.CharField(max_length=100)
    battery_capacity_kwh = models.FloatField()
    max_dc_power_kw = models.FloatField()
    max_ac_power_kw = models.FloatField()
    connector_type = models.CharField(max_length=20)
    wltp_range_km = models.FloatField(default=300)

    def __str__(self):
        return self.name


# Links one User to one Car (one-to-one)
class UserCar(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    car = models.ForeignKey(Car, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"{self.user.username} → {self.car.name}"


# Stores FCM device tokens for push notifications
class DeviceToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=300, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} -> {self.token[:20]}"
```

**What this does:**  
- `Car` stores a catalogue of EV models with specs like battery size, maximum DC/AC charging speed, connector type, and WLTP range.  
- `UserCar` is a one-to-one link that says "this user drives this car". It drives smart filtering — when a user opens the app, stations are filtered and sorted to match their connector type and preferred charger speed.  
- `DeviceToken` holds Firebase Cloud Messaging tokens. Every time a booking event happens, the system looks up the user's token here and pushes a notification.

---

## Step 2 — accounts/views.py

Handles signup, login, car selection, and device token saving.

```python
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Car, UserCar
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

# Returns the full car catalogue
class CarListView(APIView):
    def get(self, request):
        cars = Car.objects.all().values()
        return Response(list(cars))


# Lets a logged-in user pick their EV
class SelectCarView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        car_id = request.data.get("car_id")
        try:
            car = Car.objects.get(id=car_id)
        except Car.DoesNotExist:
            return Response({"error": "Invalid car ID"}, status=400)

        user_car, created = UserCar.objects.get_or_create(user=request.user)
        user_car.car = car
        user_car.save()
        return Response({"message": "Car saved successfully"})


# Returns smart recommendation (DC vs AC) based on user's car specs
class SmartCarDetailsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            user_car = UserCar.objects.get(user=request.user)
        except UserCar.DoesNotExist:
            return Response({"error": "No car selected"}, status=404)

        car = user_car.car
        if car.max_dc_power_kw >= 30:
            recommended = "DC Fast Charging"
            speed_kw = car.max_dc_power_kw
        else:
            recommended = "AC Slow Charging"
            speed_kw = car.max_ac_power_kw

        return Response({
            "name": car.name,
            "battery_capacity_kwh": car.battery_capacity_kwh,
            "connector_type": car.connector_type,
            "recommended_charger": recommended,
            "charging_speed_kw": speed_kw
        })


# Creates a new user account with validation
class SignupView(APIView):
    def post(self, request):
        username = request.data.get("username", "").strip()
        email    = request.data.get("email", "").strip()
        password = request.data.get("password", "")

        if not username or not email or not password:
            return Response({"error": "Username, email, and password are required"}, status=400)
        if len(password) < 6:
            return Response({"error": "Password must be at least 6 characters"}, status=400)
        if len(username) < 3:
            return Response({"error": "Username must be at least 3 characters"}, status=400)
        if not ("@" in email and "." in email):
            return Response({"error": "Invalid email format"}, status=400)
        if User.objects.filter(username=username).exists():
            return Response({"error": "Username already exists"}, status=400)
        if User.objects.filter(email=email).exists():
            return Response({"error": "Email already registered"}, status=400)

        user = User.objects.create_user(username=username, email=email, password=password)
        return Response({"message": "User created successfully", "user_id": user.id}, status=201)


# Returns JWT access + refresh tokens on valid credentials
class LoginView(APIView):
    def post(self, request):
        username = request.data.get("username", "").strip()
        password = request.data.get("password", "")

        if not username or not password:
            return Response({"error": "Username and password are required"}, status=400)

        user = User.objects.filter(username=username).first()
        if user is None or not user.check_password(password):
            return Response({"error": "Invalid credentials"}, status=400)

        refresh = RefreshToken.for_user(user)
        return Response({
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }, status=200)


# Saves or updates a user's FCM device token
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def save_device_token(request):
    token = request.data.get("token")
    if not token:
        return Response({"error": "token is required"}, status=400)
    DeviceToken.objects.update_or_create(user=request.user, token=token)
    return Response({"message": "Token saved"})
```

**What this does:**  
- `SignupView` validates all fields, checks for duplicate usernames/emails, then calls Django's built-in `create_user` which hashes the password securely.  
- `LoginView` authenticates with `check_password` then issues a JWT pair using `simplejwt`. The access token is short-lived; the refresh token can be used to obtain new access tokens.  
- `SmartCarDetailsView` inspects the user's car's `max_dc_power_kw`. If it is ≥ 30 kW the car supports fast DC charging; otherwise it recommends slower AC charging.

---

## Step 3 — stations/models.py

Defines all nine data models for the stations app.

```python
from django.db import models
from django.conf import settings

User = settings.AUTH_USER_MODEL


# 1. The main charging station entity
class ChargingStation(models.Model):
    name             = models.CharField(max_length=255)
    latitude         = models.FloatField()
    longitude        = models.FloatField()
    address          = models.TextField(null=True, blank=True)
    charger_type     = models.CharField(max_length=10, default="DC")   # AC or DC
    connector_type   = models.CharField(max_length=100, default="CCS2")
    power_kw         = models.FloatField(default=30.0)
    total_slots      = models.IntegerField(default=4)
    available_slots  = models.IntegerField(default=2)
    price_per_kwh    = models.FloatField(default=18.0)
    waiting_time     = models.IntegerField(default=5)
    speed            = models.IntegerField(default=50)
    status           = models.CharField(max_length=20, default="Available")
    image_url        = models.URLField(max_length=500, null=True, blank=True)
    description      = models.TextField(null=True, blank=True)
    phone_number     = models.CharField(max_length=20, null=True, blank=True)
    email            = models.EmailField(null=True, blank=True)
    operating_hours  = models.CharField(max_length=100, default="24/7")
    facilities       = models.TextField(null=True, blank=True)   # comma-separated
    is_open_24_7     = models.BooleanField(default=True)
    has_parking      = models.BooleanField(default=True)
    has_restroom     = models.BooleanField(default=False)
    has_cafe         = models.BooleanField(default=False)
    has_wifi         = models.BooleanField(default=False)
    verified         = models.BooleanField(default=True)
    last_updated     = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


# 2. A time-slot booking at a regular station
class Booking(models.Model):
    STATUS_CHOICES = (
        ("active", "Active"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    )
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    station    = models.ForeignKey(ChargingStation, on_delete=models.CASCADE)
    start_time = models.DateTimeField()
    end_time   = models.DateTimeField()
    status     = models.CharField(max_length=10, choices=STATUS_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} - {self.station} - {self.status}"


# 3. User's saved favourite stations
class FavouriteStation(models.Model):
    user     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    station  = models.ForeignKey(ChargingStation, on_delete=models.CASCADE)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "station")


# 4. P2P charger listed by a private owner
class PeerCharger(models.Model):
    owner          = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                       related_name="peer_chargers")
    name           = models.CharField(max_length=255)
    address        = models.TextField(blank=True)
    latitude       = models.FloatField()
    longitude      = models.FloatField()
    connector_type = models.CharField(max_length=50, default="CCS2")
    power_kw       = models.FloatField(default=7.0)
    price_per_kwh  = models.FloatField(default=15.0)
    is_active      = models.BooleanField(default=True)
    created_at     = models.DateTimeField(auto_now_add=True)


# 4b. A booking request against a P2P charger
class PeerBooking(models.Model):
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("cancelled", "Cancelled"),
        ("completed", "Completed"),
    )
    renter     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name="peer_bookings")
    charger    = models.ForeignKey(PeerCharger, on_delete=models.CASCADE, related_name="bookings")
    start_time = models.DateTimeField()
    end_time   = models.DateTimeField()
    status     = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)


# 5. Star ratings and text reviews
class StationRating(models.Model):
    user          = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                      related_name="station_ratings")
    station       = models.ForeignKey(ChargingStation, on_delete=models.CASCADE,
                                      related_name="ratings")
    rating        = models.IntegerField()          # 1–5
    review        = models.TextField(null=True, blank=True)
    helpful_count = models.IntegerField(default=0)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "station")


# 6. Penalty system — track no-shows and late cancellations
class UserPenalty(models.Model):
    user             = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    no_show_count    = models.IntegerField(default=0)
    late_cancel_count= models.IntegerField(default=0)
    penalty_points   = models.IntegerField(default=0)
    blocked_until    = models.DateTimeField(null=True, blank=True)
    updated_at       = models.DateTimeField(auto_now=True)


# 7. Smart waitlist queue with position and expiry
class Waitlist(models.Model):
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name="waitlist_entries")
    station    = models.ForeignKey(ChargingStation, on_delete=models.CASCADE,
                                   related_name="waitlist_entries")
    position   = models.IntegerField(default=1)
    notified   = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("user", "station")
        ordering = ["position", "created_at"]


# 8. Recently viewed stations (last 10 per user)
class RecentlyViewedStation(models.Model):
    user      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name="recently_viewed")
    station   = models.ForeignKey(ChargingStation, on_delete=models.CASCADE,
                                  related_name="viewed_by")
    viewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "station")
        ordering = ["-viewed_at"]


# 9. Crowd-sourced real-time station reports
class StationReport(models.Model):
    REPORT_TYPES = [
        ('broken',  'Charger Broken'),
        ('queue',   'Long Queue'),
        ('closed',  'Station Closed'),
        ('offline', 'Offline / No Power'),
        ('clean',   'All Clear'),
    ]
    station     = models.ForeignKey(ChargingStation, on_delete=models.CASCADE, related_name='reports')
    user        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True)
    report_type = models.CharField(max_length=20, choices=REPORT_TYPES)
    note        = models.CharField(max_length=200, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    expires_at  = models.DateTimeField()   # auto-expires after 2 hours
    upvotes     = models.IntegerField(default=0)

    class Meta:
        ordering = ['-created_at']
```

**What this does:**  
Each model maps to a database table. The most important relationships are:  
- `Booking` → `User` + `ChargingStation` (many-to-many through a junction with start/end time).  
- `PeerBooking` → `User` (renter) + `PeerCharger` (owned by a different user).  
- `UserPenalty` → `User` (one-to-one effective, tracks points and temporary blocks).  
- `Waitlist` → `User` + `ChargingStation` with a `position` integer that is always kept sequential by the waitlist service.

---

## Step 4 — stations/serializers.py

Converts Django model instances to/from JSON for the REST API.

```python
from rest_framework import serializers
from django.db.models import Avg
from django.utils import timezone
from .models import ChargingStation, Booking, FavouriteStation, PeerCharger, PeerBooking, StationRating
from accounts.models import User


class ChargingStationSerializer(serializers.ModelSerializer):
    avg_rating      = serializers.SerializerMethodField()
    facilities_list = serializers.SerializerMethodField()

    class Meta:
        model  = ChargingStation
        fields = [
            'id', 'name', 'address', 'latitude', 'longitude',
            'charger_type', 'connector_type', 'power_kw', 'price_per_kwh',
            'available_slots', 'total_slots', 'waiting_time', 'avg_rating',
            'image_url', 'description', 'phone_number', 'email', 'operating_hours',
            'facilities', 'facilities_list', 'is_open_24_7', 'has_parking',
            'has_restroom', 'has_cafe', 'has_wifi', 'verified', 'last_updated'
        ]

    def get_avg_rating(self, obj):
        avg = StationRating.objects.filter(station=obj).aggregate(Avg('rating'))['rating__avg']
        return round(avg or 0, 2)

    def get_facilities_list(self, obj):
        if obj.facilities:
            return [f.strip() for f in obj.facilities.split(',') if f.strip()]
        return []


class BookingSerializer(serializers.ModelSerializer):
    station_name      = serializers.CharField(source='station.name', read_only=True)
    station_id        = serializers.IntegerField(source='station.id', read_only=True)
    station_latitude  = serializers.FloatField(source='station.latitude', read_only=True)
    station_longitude = serializers.FloatField(source='station.longitude', read_only=True)
    station_address   = serializers.CharField(source='station.address', read_only=True, default='')
    connector_type    = serializers.CharField(source='station.connector_type', read_only=True)
    power_kw          = serializers.FloatField(source='station.power_kw', read_only=True)
    price_per_kwh     = serializers.FloatField(source='station.price_per_kwh', read_only=True)
    charger_type      = serializers.CharField(source='station.charger_type', read_only=True)
    created_at        = serializers.SerializerMethodField()
    start_time        = serializers.SerializerMethodField()
    end_time          = serializers.SerializerMethodField()

    class Meta:
        model  = Booking
        fields = [
            'id', 'station_id', 'station_name', 'station_latitude', 'station_longitude',
            'station_address', 'connector_type', 'power_kw', 'price_per_kwh', 'charger_type',
            'start_time', 'end_time', 'status', 'created_at'
        ]

    # All datetimes are converted to local timezone before formatting
    def get_created_at(self, obj):
        return timezone.localtime(obj.created_at).strftime('%Y-%m-%d %H:%M') if obj.created_at else None

    def get_start_time(self, obj):
        return timezone.localtime(obj.start_time).strftime('%Y-%m-%d %H:%M') if obj.start_time else None

    def get_end_time(self, obj):
        return timezone.localtime(obj.end_time).strftime('%Y-%m-%d %H:%M') if obj.end_time else None


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model  = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name']


class FavouriteStationSerializer(serializers.ModelSerializer):
    station = ChargingStationSerializer(read_only=True)   # nested full station data

    class Meta:
        model  = FavouriteStation
        fields = ['id', 'station', 'added_at']


class PeerChargerSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source='owner.username', read_only=True)

    class Meta:
        model  = PeerCharger
        fields = [
            'id', 'name', 'owner', 'owner_name', 'latitude', 'longitude',
            'address', 'connector_type', 'power_kw', 'price_per_kwh',
            'is_active', 'created_at'
        ]
        read_only_fields = ['id', 'owner', 'owner_name', 'created_at']


class PeerBookingSerializer(serializers.ModelSerializer):
    charger    = PeerChargerSerializer(read_only=True)
    charger_id = serializers.PrimaryKeyRelatedField(
        queryset=PeerCharger.objects.filter(is_active=True),
        source='charger', write_only=True,
    )

    class Meta:
        model  = PeerBooking
        fields = ['id', 'charger', 'charger_id', 'renter', 'start_time', 'end_time', 'status', 'created_at']
        read_only_fields = ['id', 'renter', 'status', 'created_at', 'charger']
```

**What this does:**  
- `ChargingStationSerializer` calculates `avg_rating` on the fly with an `Avg` database aggregation and splits the comma-separated `facilities` string into a clean list (`facilities_list`).  
- `BookingSerializer` flattens related station fields (name, lat, lng, price, etc.) into the booking response so the client does not need to make a second request.  
- All datetime fields are converted to local timezone (Asia/Kolkata) using `timezone.localtime` before being formatted, so UTC timestamps stored in the database are always returned in the correct local time.

---

## Step 5 — stations/topsis.py

Implements the TOPSIS (Technique for Order of Preference by Similarity to Ideal Solution) multi-criteria decision-making algorithm.

```python
import numpy as np

def topsis(matrix, weights, impacts):
    """
    matrix:  2D list — rows are stations, columns are criteria
    weights: list of numbers (one per criterion)
    impacts: list of '+' (higher is better) or '-' (lower is better)
    Returns: list of scores between 0 and 1 (higher = better station)
    """
    matrix = np.array(matrix, dtype=float)
    rows, cols = matrix.shape

    # Step 1: Normalise each column (divide each value by the column's Euclidean norm)
    norm = np.sqrt((matrix ** 2).sum(axis=0))
    normalized = matrix / norm

    # Step 2: Multiply each column by its weight
    weighted = normalized * weights

    # Step 3: Identify ideal best and ideal worst for each criterion
    ideal_best  = []
    ideal_worst = []
    for j in range(cols):
        if impacts[j] == '+':
            ideal_best.append(weighted[:, j].max())
            ideal_worst.append(weighted[:, j].min())
        else:
            ideal_best.append(weighted[:, j].min())
            ideal_worst.append(weighted[:, j].max())

    ideal_best  = np.array(ideal_best)
    ideal_worst = np.array(ideal_worst)

    # Step 4: Euclidean distance from each station to ideal best/worst
    d_best  = np.sqrt(((weighted - ideal_best)  ** 2).sum(axis=1))
    d_worst = np.sqrt(((weighted - ideal_worst) ** 2).sum(axis=1))

    # Step 5: TOPSIS score = d_worst / (d_best + d_worst)
    # A station closer to ideal best and farther from ideal worst gets a score near 1
    denominator = d_best + d_worst
    scores = np.divide(d_worst, denominator, where=denominator != 0,
                       out=np.full_like(d_worst, 0.5))
    scores = np.nan_to_num(scores, nan=0.5)

    return scores.tolist()
```

**What this does:**  
The function takes a matrix where each row is a candidate charging station and each column is a criterion (available slots, power, waiting time, estimated charging time, price per kWh, distance from user). Weights and impact directions are provided by the caller. It returns a score between 0 and 1 for every station — higher means a better overall choice given the user's preferences. This score is used both in the `/api/stations/topsis/` ranking endpoint and in the `/api/stations/search/` text-search results.

---

## Step 6 — stations/views.py (Part A — Utility Functions)

Helper functions shared across many API views.

```python
import math
import numpy as np
from django.utils import timezone
from datetime import timedelta
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

# Haversine formula — great-circle distance between two GPS coordinates
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# Returns stations within radius, sorted by distance
def _get_nearby_station_tuples(user_lat, user_lng, radius_km):
    result = []
    for st in ChargingStation.objects.all():
        dist = calculate_distance(user_lat, user_lng, st.latitude, st.longitude)
        if dist <= radius_km:
            result.append((st, dist))
    result.sort(key=lambda x: x[1])
    return result


# Real-time slot count: total slots minus currently active booking count
def get_available_slots_now(station):
    now = timezone.now()
    active_count = Booking.objects.filter(
        station=station, status="active",
        start_time__lte=now, end_time__gt=now
    ).count()
    return max(0, station.total_slots - active_count)


# Slot count for a future time window (for new booking checks)
def get_available_slots_at_time(station, start_time, end_time):
    overlapping = Booking.objects.filter(
        station=station, status="active",
        start_time__lt=end_time, end_time__gt=start_time,
    ).count()
    return max(0, station.total_slots - overlapping)


# Parses ISO datetime string and makes it timezone-aware
def parse_iso_datetime(dt_str):
    from datetime import datetime
    dt = datetime.fromisoformat(dt_str)
    tz = timezone.get_current_timezone()
    if dt.tzinfo is None:
        return timezone.make_aware(dt, tz)
    return dt.astimezone(tz)


# Broadcasts real-time slot count to all WebSocket clients watching a station
def broadcast_slot_update(station_id):
    try:
        channel_layer = get_channel_layer()
        station = ChargingStation.objects.get(id=station_id)
        now = timezone.now()
        active_bookings = Booking.objects.filter(
            station=station, start_time__lte=now, end_time__gte=now, status='active'
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
            {'type': 'slots_update', 'data': data}
        )
    except Exception as e:
        print(f"[Broadcast Error] {e}")


# Adds penalty points and optionally blocks the user (max 5 minutes)
def add_penalty(user, points):
    pen, _ = UserPenalty.objects.get_or_create(user=user)
    pen.penalty_points += points
    if pen.penalty_points >= 3:
        pen.blocked_until = timezone.now() + timedelta(minutes=5)
    pen.save()
```

**What this does:**  
- `calculate_distance` uses the Haversine formula which accounts for Earth's curvature to give accurate distances in kilometres between two GPS coordinates.  
- `broadcast_slot_update` is called after every booking creation or cancellation to push updated slot counts to all WebSocket clients via Django Channels channel groups.  
- `add_penalty` ensures a user is never blocked for more than 5 minutes, making the penalty system firm but not punitive.

---

## Step 7 — stations/views.py (Part B — Booking APIs)

The core booking creation and cancellation logic.

```python
# POST /api/stations/book/
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_booking(request):
    user = request.user

    # 1. Check if user is currently blocked
    pen = UserPenalty.objects.filter(user=user).first()
    if pen and pen.blocked_until and pen.blocked_until > timezone.now():
        max_block = timezone.now() + timedelta(minutes=5)
        if pen.blocked_until > max_block:
            pen.blocked_until = max_block
            pen.save()
        local_block = timezone.localtime(pen.blocked_until)
        return Response(
            {"error": f"You are blocked until {local_block.strftime('%Y-%m-%d %H:%M')}"},
            status=403
        )

    station_id = request.data.get("station_id")
    start_time = request.data.get("start_time")
    end_time   = request.data.get("end_time")

    if not all([station_id, start_time, end_time]):
        return Response({"error": "station_id, start_time, end_time required"}, status=400)

    start = parse_iso_datetime(start_time)
    end   = parse_iso_datetime(end_time)
    if end <= start:
        return Response({"error": "Invalid time range"}, status=400)

    station = ChargingStation.objects.get(id=station_id)

    # 2. Count overlapping bookings in the requested window
    overlapping = Booking.objects.filter(
        station=station, status="active",
        start_time__lt=end, end_time__gt=start,
    ).count()

    # 3a. Station is full → add to waitlist
    if overlapping >= station.total_slots:
        existing = Waitlist.objects.filter(user=user, station=station).first()
        if existing:
            return Response({"message": "Already in waitlist.", "position": existing.position}, status=202)

        last    = Waitlist.objects.filter(station=station).order_by("-position").first()
        next_pos = (last.position + 1) if last else 1
        expires_at = timezone.now() + timedelta(minutes=30)
        Waitlist.objects.create(user=user, station=station, position=next_pos, expires_at=expires_at)

        # Email and push notification
        threading.Thread(target=send_waitlist_notification_email, args=(user, station, next_pos), daemon=True).start()
        for t in DeviceToken.objects.filter(user=user):
            send_push_notification(t.token, title="Added to Waitlist",
                body=f"Station {station.name} is full. Your waitlist position: {next_pos}")
        return Response({"message": "Station full. Added to waitlist.", "position": next_pos}, status=202)

    # 3b. Slot available → create booking
    booking = Booking.objects.create(user=user, station=station,
                                     start_time=start, end_time=end, status="active")
    station.available_slots = max(0, station.available_slots - 1)
    station.save()

    # Broadcast real-time update
    broadcast_slot_update(station.id)

    # Send confirmation email asynchronously
    threading.Thread(target=send_booking_confirmation_email,
                     args=(user, booking, station), daemon=True).start()

    # Push notification
    for t in DeviceToken.objects.filter(user=user):
        send_push_notification(t.token, title="Booking Confirmed",
            body=f"Booked {station.name} from {start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}")

    return Response({"message": "Booking created", "booking_id": booking.id})


# POST /api/stations/cancel/
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def cancel_booking(request):
    booking_id = request.data.get("booking_id")
    booking    = Booking.objects.get(id=booking_id, user=request.user)

    if booking.status != "active":
        return Response({"error": "Only active bookings can be cancelled"}, status=400)

    now_ = timezone.now()
    if booking.start_time <= now_:
        return Response({"error": "Booking already started"}, status=400)

    # Late cancellation penalty — within 10 minutes of start
    if booking.start_time - now_ <= timedelta(minutes=10):
        pen, _ = UserPenalty.objects.get_or_create(user=request.user)
        pen.late_cancel_count += 1
        pen.penalty_points    += 1
        pen.save()

    station = booking.station
    booking.status = "cancelled"
    booking.save()

    station.available_slots = min(station.total_slots, station.available_slots + 1)
    station.save()

    broadcast_slot_update(station.id)

    # Push notification to user
    for t in DeviceToken.objects.filter(user=request.user):
        send_push_notification(t.token, title="Booking Cancelled",
            body=f"Your booking at {station.name} has been cancelled.")

    # Immediately promote first person in waitlist
    promote_waitlist_for_station(station, notify=True)

    return Response({"message": "Booking cancelled"})
```

**What this does:**  
- `create_booking` performs three important checks sequentially: (1) is the user blocked by the penalty system, (2) does the requested time window overlap with enough existing bookings to fill all slots, (3) if not full then create the booking and do post-creation work (decrement slots, WebSocket broadcast, async email, push notification). If the station is full it creates a `Waitlist` entry instead and returns HTTP 202.  
- `cancel_booking` checks that the booking hasn't yet started, applies a 1-point late-cancel penalty if cancelled within 10 minutes of the start, then immediately calls `promote_waitlist_for_station` so the first person in the queue gets a booking right away.

---

## Step 8 — stations/views.py (Part C — TOPSIS Ranking API)

```python
# POST /api/stations/topsis/
@api_view(["POST"])
def topsis_custom(request):
    user_lat    = float(request.data.get("lat"))
    user_lng    = float(request.data.get("lng"))
    radius      = float(request.data.get("radius", 15))
    top_n       = int(request.data.get("top_n", 5))
    weights_dict = request.data.get("weights") or {}

    required = ["available_slots", "power_kw", "waiting_time",
                "charging_time", "price_per_kwh", "distance"]
    for k in required:
        if k not in weights_dict:
            return Response({"error": f"Weight '{k}' missing"}, status=400)

    weights = [float(weights_dict[k]) for k in required]

    # Collect stations within radius that have at least one free slot
    nearby = []
    for st in ChargingStation.objects.all():
        dist = calculate_distance(user_lat, user_lng, st.latitude, st.longitude)
        if dist <= radius and st.available_slots > 0:
            st._distance = dist
            nearby.append(st)

    if not nearby:
        return Response({"error": "No available stations near your location"}, status=404)

    matrix = []
    data   = []
    for st in nearby:
        charging_time = st.power_kw / st.speed if getattr(st, "speed", 0) else 0
        matrix.append([st.available_slots, st.power_kw, st.waiting_time,
                        charging_time, st.price_per_kwh, st._distance])
        data.append({
            "id": st.id, "name": st.name,
            "latitude": float(st.latitude), "longitude": float(st.longitude),
            "available_slots": st.available_slots, "power_kw": st.power_kw,
            "waiting_time": st.waiting_time, "charging_time": round(charging_time, 2),
            "price_per_kwh": st.price_per_kwh, "distance": round(st._distance, 2),
        })

    # Impacts: slots(+), power(+), waiting(-), charging_time(-), price(-), distance(-)
    impacts = ["+", "+", "-", "-", "-", "-"]
    scores  = topsis(matrix, weights, impacts)
    for i, s in enumerate(scores):
        data[i]["score"] = round(s, 4)

    ranked = sorted(data, key=lambda x: x["score"], reverse=True)
    return Response(ranked[:top_n])
```

**What this does:**  
The client sends user location, a search radius, `top_n` (how many results to return), and a `weights` dictionary specifying how much each criterion matters to the user. The backend builds a decision matrix (one row per nearby station), runs TOPSIS, attaches a `score` to each station, and returns the `top_n` highest-scoring stations. This enables highly personalised "best station for me right now" recommendations.

---

## Step 9 — stations/views.py (Part D — Map, Route, P2P, Reports)

```python
# GET /api/stations/map/?lat=&lng=&radius=
# Returns lightweight marker data (no heavy serializer) for map pins
@api_view(["GET"])
def map_nearby_stations(request):
    user_lat = float(request.GET.get("lat"))
    user_lng = float(request.GET.get("lng"))
    radius   = float(request.GET.get("radius", 10))
    tuples   = _get_nearby_station_tuples(user_lat, user_lng, radius)
    return Response([{
        "id": st.id, "name": st.name,
        "latitude": st.latitude, "longitude": st.longitude,
        "distance_km": round(dist, 2),
    } for st, dist in tuples])


# POST /api/stations/route/  — stations within distance of a route polyline
@api_view(["POST"])
def stations_along_route(request):
    route     = request.data.get("route")   # list of {lat, lng} points
    radius    = float(request.data.get("radius", 2))
    connector = request.data.get("connector")

    qs = ChargingStation.objects.all()
    if connector:
        qs = qs.filter(connector_type=connector)

    result = []
    for st in qs:
        dist = min_distance_to_route(st, route)   # perpendicular distance to path
        if dist <= radius:
            result.append({"id": st.id, "name": st.name,
                           "lat": st.latitude, "lng": st.longitude,
                           "distance_from_route": round(dist, 2),
                           "available_slots": st.available_slots,
                           "power_kw": st.power_kw, "price_per_kwh": st.price_per_kwh})
    return Response(result)


# POST /api/stations/p2p/create/  — list a private charger
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def p2p_create_charger(request):
    serializer = PeerChargerSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save(owner=request.user)
        return Response(serializer.data, status=201)
    return Response(serializer.errors, status=400)


# POST /api/stations/p2p/book/  — book a private charger
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def p2p_create_booking(request):
    charger_id = request.data.get("charger_id")
    charger    = PeerCharger.objects.get(id=charger_id, is_active=True)
    start      = parse_iso_datetime(request.data.get("start_time"))
    end        = parse_iso_datetime(request.data.get("end_time"))

    # Overlap check — no double bookings
    overlap = PeerBooking.objects.filter(
        charger=charger, status__in=["pending", "approved"],
        start_time__lt=end, end_time__gt=start,
    ).exists()
    if overlap:
        return Response({"error": "Slot not available"}, status=409)

    booking = PeerBooking.objects.create(renter=request.user, charger=charger,
                                         start_time=start, end_time=end, status="pending")

    # Notify the charger owner via push
    for t in DeviceToken.objects.filter(user=charger.owner):
        send_push_notification(t.token, title="New P2P booking request",
            body=f"{request.user.username} requested your charger '{charger.name}'")

    return Response({"message": "Booking created, waiting for owner approval",
                     "booking_id": booking.id})


# POST /api/stations/p2p/approve/  — owner approves or rejects a booking
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def p2p_approve(request):
    booking_id = request.data.get("booking_id")
    action     = request.data.get("action")  # "approve" or "reject"
    booking    = PeerBooking.objects.get(id=booking_id)

    if booking.charger.owner != request.user:
        return Response({"error": "Not authorized"}, status=403)

    booking.status = "approved" if action == "approve" else "rejected"
    booking.save()

    # Notify the renter
    status_text = "approved" if action == "approve" else "rejected"
    for t in DeviceToken.objects.filter(user=booking.renter):
        send_push_notification(t.token, title=f"P2P booking {status_text}",
            body=f"Your booking for {booking.charger.name} was {status_text}.")
    return Response({"message": f"Booking {status_text}."})


# POST /api/stations/report/  — submit a crowd-sourced station problem report
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def submit_report(request):
    station_id  = request.data.get("station_id")
    report_type = request.data.get("report_type")   # broken / queue / closed / offline / clean
    note        = request.data.get("note", "")[:200]

    station    = ChargingStation.objects.get(id=station_id)
    expires_at = timezone.now() + timedelta(hours=2)
    report     = StationReport.objects.create(
        station=station, user=request.user,
        report_type=report_type, note=note, expires_at=expires_at,
    )
    return Response({"message": "Report submitted", "report_id": report.id,
                     "expires_at": expires_at.isoformat()})


# POST /api/stations/report/upvote/  — upvote an existing active report
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def upvote_report(request):
    report_id = request.data.get("report_id")
    r = StationReport.objects.get(id=report_id, expires_at__gt=timezone.now())
    r.upvotes += 1
    r.save(update_fields=['upvotes'])
    return Response({"upvotes": r.upvotes})


# GET /api/stations/geocode/?q=  — proxies Nominatim address search (India only)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def geocode_search(request):
    q = request.query_params.get('q', '').strip()
    resp = requests.get('https://nominatim.openstreetmap.org/search',
        params={'q': q, 'format': 'json', 'limit': '7', 'countrycodes': 'in'},
        headers={'User-Agent': 'ObturoEV-Backend/1.0'}, timeout=10)
    results = resp.json()
    return Response([{'name': r.get('name',''), 'display_name': r.get('display_name',''),
                      'lat': r.get('lat'), 'lon': r.get('lon')} for r in results])


# GET /api/stations/route/osrm/  — proxies OSRM turn-by-turn routing
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def osrm_proxy(request):
    origin_lat = request.query_params.get('origin_lat')
    origin_lng = request.query_params.get('origin_lng')
    dest_lat   = request.query_params.get('dest_lat')
    dest_lng   = request.query_params.get('dest_lng')
    url = (f'https://router.project-osrm.org/route/v1/driving/'
           f'{origin_lng},{origin_lat};{dest_lng},{dest_lat}?overview=full&geometries=geojson')
    resp = requests.get(url, timeout=20)
    return Response(resp.json())
```

**What this does:**  
- Map and route APIs provide GeoJSON-friendly payloads for the Flutter map view.  
- `min_distance_to_route` computes true perpendicular distance from a station to each segment of a polyline using the point-to-line-segment formula, ensuring only stations genuinely near the route are returned.  
- The P2P system is a complete two-sided marketplace: owners list chargers, renters request bookings, owners approve/reject, and both sides receive push notifications at every step.  
- Reports expire after 2 hours automatically (the query always filters `expires_at__gt=now`). Users can upvote reports to increase their visibility.  
- `geocode_search` and `osrm_proxy` act as backend proxies so the mobile app does not need direct internet access to third-party services.

---

## Step 10 — stations/views.py (Part E — Admin Analytics)

```python
# GET /api/stations/admin/stats/  — admin dashboard overview
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_dashboard_stats(request):
    if not request.user.is_staff:
        return Response({"error": "Admin access required"}, status=403)

    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    seven_days_ago  = now - timedelta(days=7)

    # Revenue estimate: duration × 30 kW average × price/kWh
    completed_bookings = Booking.objects.filter(status="completed")
    estimated_revenue  = sum(
        ((b.end_time - b.start_time).total_seconds() / 3600) * 30 * b.station.price_per_kwh
        for b in completed_bookings
    )

    total_capacity = ChargingStation.objects.aggregate(Sum('total_slots'))['total_slots__sum'] or 0
    total_available= ChargingStation.objects.aggregate(Sum('available_slots'))['available_slots__sum'] or 0
    utilization_rate = ((total_capacity - total_available) / total_capacity * 100) if total_capacity else 0

    top_stations = Booking.objects.values('station__name', 'station__id') \
        .annotate(booking_count=Count('id')).order_by('-booking_count')[:5]

    return Response({
        'overview': {
            'total_users': User.objects.count(),
            'total_stations': ChargingStation.objects.count(),
            'total_bookings': Booking.objects.count(),
            'active_bookings': Booking.objects.filter(status="active").count(),
            'estimated_revenue': round(estimated_revenue, 2),
            'utilization_rate': round(utilization_rate, 2),
        },
        'recent_activity': {
            'new_users_30d': User.objects.filter(date_joined__gte=thirty_days_ago).count(),
            'bookings_30d': Booking.objects.filter(created_at__gte=thirty_days_ago).count(),
            'bookings_7d': Booking.objects.filter(created_at__gte=seven_days_ago).count(),
        },
        'top_stations': list(top_stations),
    })
```

**What this does:**  
The admin analytics endpoints (stats, revenue, user management, booking analytics, station management) are all guarded by `request.user.is_staff`. They calculate KPIs like estimated revenue (assuming 30 kW average draw × duration × price per kWh), system-wide utilisation rate (booked slots ÷ total slots), top stations by booking volume, daily and monthly revenue breakdowns, booking cancellation rates, and peak usage hours.

---

## Step 11 — stations/waitlist_service.py

Centralised waitlist management used by both the booking views and the scheduler.

```python
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from .models import Waitlist, Booking, ChargingStation
from accounts.models import DeviceToken
from .firebase import send_push_notification


# Renumbers positions 1, 2, 3, ... after any deletion
def reorder_waitlist(station):
    entries = Waitlist.objects.filter(station=station).order_by("created_at", "id")
    with transaction.atomic():
        for idx, entry in enumerate(entries, start=1):
            if entry.position != idx:
                entry.position = idx
                entry.save(update_fields=["position"])


# Returns position + estimated wait minutes for a user
def get_waitlist_info(user, station):
    try:
        entry = Waitlist.objects.get(user=user, station=station)
    except Waitlist.DoesNotExist:
        return None
    return {"position": entry.position,
            "estimated_wait_minutes": estimate_wait_time(station, entry.position)}


# Heuristic: average completed booking duration × queue position
def estimate_wait_time(station, user_position):
    recent = Booking.objects.filter(station=station, status="completed").order_by("-end_time")[:10]
    if recent.exists():
        durations = [(b.end_time - b.start_time).total_seconds() / 60.0 for b in recent]
        avg_duration = sum(durations) / len(durations)
    else:
        avg_duration = 30   # 30-minute fallback
    return round(user_position * avg_duration)


# Promotes up to `free_slots` users from the waitlist into real bookings
def promote_waitlist_for_station(station, notify=True, max_promote=None):
    active_count = Booking.objects.filter(station=station, status="active").count()
    free_slots   = station.total_slots - active_count
    if free_slots <= 0:
        return [], []

    to_promote = free_slots if max_promote is None else min(max_promote, free_slots)
    waiting    = Waitlist.objects.filter(station=station).order_by("position", "created_at")[:to_promote]

    promoted_bookings = []
    for entry in waiting:
        with transaction.atomic():
            start   = timezone.now() + timedelta(minutes=5)
            end     = start + timedelta(minutes=30)
            booking = Booking.objects.create(user=entry.user, station=station,
                                             start_time=start, end_time=end, status="active")
            promoted_bookings.append(booking.id)
            entry.delete()

            if notify:
                for t in DeviceToken.objects.filter(user=booking.user):
                    send_push_notification(t.token, title="Slot Available",
                        body=f"You have been promoted from the waitlist at {station.name}. "
                             f"Slot from {start.strftime('%H:%M')} to {end.strftime('%H:%M')}.")

    reorder_waitlist(station)
    return promoted_bookings, []
```

**What this does:**  
- `reorder_waitlist` is called after every deletion to keep positions clean (1, 2, 3, ...).  
- `promote_waitlist_for_station` is the critical function that converts waitlist entries into real bookings. It is called both when a user cancels (freeing a slot immediately) and by the scheduler when completed bookings free slots. Each promotion creates a 30-minute booking starting 5 minutes from now and sends a push notification.

---

## Step 12 — stations/consumers.py

Django Channels WebSocket consumer for real-time slot updates.

```python
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import ChargingStation, Booking
from django.utils import timezone


class StationSlotsConsumer(AsyncWebsocketConsumer):
    """
    WebSocket endpoint: ws://host/ws/station/{station_id}/
    All clients watching the same station are in group 'station_{id}'.
    """

    async def connect(self):
        self.station_id      = self.scope['url_route']['kwargs']['station_id']
        self.room_group_name = f'station_{self.station_id}'

        # Join the channel group for this station
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # Send current state immediately on connect
        station_data = await self.get_station_data()
        await self.send(text_data=json.dumps({'type': 'initial_data', 'data': station_data}))

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def slots_update(self, event):
        """Called by channel layer when broadcast_slot_update() fires."""
        await self.send(text_data=json.dumps({'type': 'slots_update', 'data': event['data']}))

    @database_sync_to_async
    def get_station_data(self):
        """Fetch current slot count (synchronous DB call wrapped for async)."""
        station = ChargingStation.objects.get(id=self.station_id)
        now     = timezone.now()
        active  = Booking.objects.filter(station=station,
                                          start_time__lte=now, end_time__gte=now,
                                          status='active').count()
        return {
            'id': station.id, 'name': station.name,
            'available_slots': station.available_slots, 'total_slots': station.total_slots,
            'active_bookings': active, 'status': station.status,
            'price_per_kwh': float(station.price_per_kwh), 'power_kw': float(station.power_kw),
        }
```

**What this does:**  
When a client (Flutter app or browser) connects, it immediately receives the current station state. After that it stays subscribed to the channel group `station_{id}`. Any time `broadcast_slot_update(station_id)` is called in a view (on booking creation or cancellation), Django Channels pushes the update to every connected client via `group_send` → `slots_update`, which calls each consumer's `slots_update` method and sends the JSON over the WebSocket. This gives all users a live, no-polling slot counter.

---

## Step 13 — stations/firebase.py

Firebase Cloud Messaging push notification integration.

```python
import os
from django.conf import settings
from firebase_admin import credentials, initialize_app, messaging

cred_path = os.path.join(settings.BASE_DIR, "firebase_key.json")
cred      = credentials.Certificate(cred_path)

# Only initialise once (guard against repeated app.ready() calls)
try:
    initialize_app(cred)
except ValueError:
    pass   # Already initialised


def send_push_notification(token: str, title: str, body: str):
    """Sends a single FCM notification to one device token."""
    message = messaging.Message(
        token=token,
        notification=messaging.Notification(title=title, body=body)
    )
    messaging.send(message)
```

**What this does:**  
Loads the Firebase service account key from `firebase_key.json` at startup and initialises the Firebase Admin SDK. `send_push_notification` is called throughout the codebase (booking confirmation, cancellation, waitlist promotion, P2P approval, 10-minute reminders) to deliver real-time push notifications to the user's mobile device.

---

## Step 14 — stations/email_service.py

Sends HTML booking confirmation and waitlist notification emails.

```python
from django.core.mail import send_mail
from django.conf import settings


def send_booking_confirmation_email(user, booking, station):
    """
    Builds and sends an HTML email with full booking details.
    Called asynchronously via threading.Thread so it never blocks the API response.
    """
    subject = f"⚡ Booking Confirmation - {station.name}"

    html_message = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
      <div style="max-width:600px; margin:0 auto; background:#f9f9f9; padding:20px;">
        <h1 style="color:#0052cc;">⚡ Obturo</h1>
        <h2>Booking Confirmed!</h2>
        <p>Hi {user.first_name or user.username},</p>
        <p><strong>Booking ID:</strong> #{booking.id}</p>
        <p><strong>Station:</strong> {station.name}</p>
        <p><strong>Address:</strong> {station.address}</p>
        <p><strong>Check-in:</strong> {booking.start_time.strftime('%B %d, %Y at %I:%M %p')}</p>
        <p><strong>Check-out:</strong> {booking.end_time.strftime('%B %d, %Y at %I:%M %p')}</p>
        <p><strong>Charger:</strong> {station.charger_type} / {station.connector_type} / {station.power_kw} kW</p>
        <p><strong>Price:</strong> ₹{station.price_per_kwh}/kWh</p>
      </div>
    </body></html>
    """

    send_mail(
        subject=subject,
        message=f"Booking confirmed for {station.name}",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
        fail_silently=True,
    )


def send_waitlist_notification_email(user, station, position):
    """Notifies the user they have been added to the waitlist at a given position."""
    subject = f"📋 Waitlist Position #{position} - {station.name}"
    html_message = f"""
    <html><body>
      <h2>You're on the waitlist!</h2>
      <p>Hi {user.first_name or user.username},</p>
      <p>Station <strong>{station.name}</strong> is currently full.</p>
      <p>Your waitlist position: <strong>#{position}</strong></p>
      <p>You will receive a notification when a slot becomes available.</p>
    </body></html>
    """
    send_mail(subject=subject,
              message=f"You are #{position} on the waitlist for {station.name}",
              from_email=settings.DEFAULT_FROM_EMAIL,
              recipient_list=[user.email],
              html_message=html_message,
              fail_silently=True)
```

**What this does:**  
Both functions are always called inside a `threading.Thread(daemon=True)` so they run in the background and do not delay the API response. `fail_silently=True` means an SMTP error does not crash the booking flow.

---

## Step 15 — stations/scheduler.py

APScheduler background jobs that run every minute.

```python
from apscheduler.schedulers.background import BackgroundScheduler
from django.utils.timezone import now
from datetime import timedelta
from django.utils import timezone
from accounts.models import DeviceToken
from .models import Booking, UserPenalty, Waitlist
from .firebase import send_push_notification


def add_penalty(user, points):
    """Adds penalty points and auto-blocks user if total reaches 3."""
    pen, _ = UserPenalty.objects.get_or_create(user=user)
    pen.penalty_points += points
    pen.no_show_count  += 1
    if pen.penalty_points >= 3:
        pen.blocked_until = timezone.now() + timedelta(minutes=5)
    pen.save()


def send_booking_reminders():
    """Sends a push notification 10 minutes before each upcoming booking."""
    upcoming = Booking.objects.filter(
        status="active",
        start_time__lte=now() + timedelta(minutes=10),
        start_time__gte=now()
    )
    for b in upcoming:
        for t in DeviceToken.objects.filter(user=b.user):
            send_push_notification(t.token, title="Charging Reminder",
                body=f"Your charging at {b.station.name} starts in 10 minutes.")


def mark_completed_bookings():
    """
    Finds all bookings whose end_time has passed, marks them completed,
    applies no-show penalty if the session was never started, frees the slot,
    and promotes the next person from the waitlist.
    """
    expired = Booking.objects.filter(status="active", end_time__lt=now())
    for b in expired:
        if b.start_time + timedelta(minutes=10) < now():
            add_penalty(b.user, 1)   # no-show penalty

        b.status = "completed"
        b.save()

        station = b.station
        station.available_slots += 1
        station.save()

        # Promote next person in queue
        from .waitlist_service import promote_waitlist_for_station
        promote_waitlist_for_station(station, notify=True)


_scheduler = None

def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return   # Prevent duplicate scheduler on hot-reload

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(send_booking_reminders,    "interval", minutes=1)
    _scheduler.add_job(mark_completed_bookings,   "interval", minutes=1)
    _scheduler.start()
    print("[Scheduler] APScheduler started.")
```

**What this does:**  
`start_scheduler()` is called from `stations/apps.py` inside `AppConfig.ready()` so it starts once when Django boots. Every minute it:  
1. Finds upcoming bookings within 10 minutes and sends reminders.  
2. Finds all expired active bookings, marks them completed, gives a no-show penalty if the user never showed up, restores the slot count, and calls `promote_waitlist_for_station` to fill the freed slot from the queue.  
The `global _scheduler` guard prevents double-start on Django's development server auto-reloader.

---

## Summary of All API Endpoints

| Method | URL | Auth | Description |
|--------|-----|------|-------------|
| POST | `/api/accounts/signup/` | No | Create account |
| POST | `/api/accounts/login/` | No | Get JWT tokens |
| GET | `/api/accounts/cars/` | No | Car catalogue |
| POST | `/api/accounts/select-car/` | Yes | Save user's EV |
| GET | `/api/accounts/smart-car/` | Yes | DC vs AC recommendation |
| GET | `/api/stations/all/` | No | All stations |
| GET | `/api/stations/nearby/` | No | Nearby stations with distance |
| POST | `/api/stations/book/` | Yes | Create booking or join waitlist |
| POST | `/api/stations/cancel/` | Yes | Cancel booking |
| GET | `/api/stations/my-bookings/` | Yes | Booking history |
| GET | `/api/stations/active-bookings/` | Yes | Active bookings for sidebar |
| POST | `/api/stations/waitlist/join/` | Yes | Join waitlist |
| GET | `/api/stations/waitlist/position/` | Yes | Waitlist position + ETA |
| POST | `/api/stations/waitlist/leave/` | Yes | Leave waitlist |
| POST | `/api/stations/topsis/` | No | TOPSIS ranked recommendations |
| GET | `/api/stations/smart/` | Yes | Car-filtered smart stations |
| GET | `/api/stations/search/` | No | Text search + TOPSIS score |
| GET | `/api/stations/map/` | No | Lightweight map markers |
| POST | `/api/stations/route/` | No | Stations along a polyline route |
| POST | `/api/stations/best-stops/` | No | Top scored stops along route |
| GET | `/api/stations/<id>/` | No | Station detail |
| POST | `/api/stations/rate/` | Yes | Submit or update rating |
| GET | `/api/stations/rating/<id>/` | No | Station ratings + reviews |
| POST | `/api/stations/view/` | Yes | Track recently viewed |
| GET | `/api/stations/recently-viewed/` | Yes | User's recent stations |
| POST | `/api/stations/favourites/toggle/` | Yes | Favourite / unfavourite |
| GET | `/api/stations/favourites/` | Yes | User's favourites |
| POST | `/api/stations/p2p/create/` | Yes | List private charger |
| GET | `/api/stations/p2p/nearby/` | No | Nearby private chargers |
| POST | `/api/stations/p2p/book/` | Yes | Request P2P booking |
| POST | `/api/stations/p2p/approve/` | Yes | Owner approve/reject |
| GET | `/api/stations/p2p/my-bookings/` | Yes | Renter's P2P history |
| GET | `/api/stations/p2p/requests/` | Yes | Owner's pending requests |
| POST | `/api/stations/report/` | Yes | Submit crowd report |
| GET | `/api/stations/reports/<id>/` | No | Active reports for station |
| GET | `/api/stations/reports/all/` | No | All active reports |
| POST | `/api/stations/report/upvote/` | Yes | Upvote a report |
| GET | `/api/stations/geocode/` | Yes | Address search (Nominatim proxy) |
| GET | `/api/stations/route/osrm/` | Yes | Routing (OSRM proxy) |
| GET | `/api/stations/tiles/<z>/<x>/<y>/` | No | Map tiles (CartoCDN proxy) |
| GET | `/api/stations/admin/stats/` | Staff | Dashboard KPIs |
| GET | `/api/stations/admin/revenue/` | Staff | Revenue analytics |
| GET | `/api/stations/admin/users/` | Staff | User management |
| GET | `/api/stations/admin/bookings/` | Staff | Booking analytics |
| GET | `/api/stations/admin/stations/` | Staff | Station management |
| WebSocket | `ws/station/<id>/` | No | Real-time slot updates |
