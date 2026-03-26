import requests
import math
from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth import login as django_login, logout as django_logout, authenticate
from django.http import JsonResponse
from django.db.models import Count
from django.utils import timezone as dj_tz
from rest_framework_simplejwt.tokens import RefreshToken
from accounts.models import UserCar, Car
from stations.models import Booking as StationBooking

API_BASE = "http://127.0.0.1:8000"  # backend base URL


def get_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two coordinates in km using Haversine formula"""
    R = 6371  # Earth's radius in km
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat/2) * math.sin(dLat/2) + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon/2) * math.sin(dLon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        user = authenticate(request, username=username, password=password)
        if user is not None:
            refresh = RefreshToken.for_user(user)
            request.session["token"] = str(refresh.access_token)
            request.session["username"] = user.username
            user.backend = "django.contrib.auth.backends.ModelBackend"
            django_login(request, user)
            request.session.save()
            return redirect("home")

        return render(request, "web/login.html", {"error": "Invalid username or password"})

    return render(request, "web/login.html")

def get_current_user(token):
    """Get current user from token"""
    try:
        res = requests.get(
            f"{API_BASE}/auth/user/",
            headers={"Authorization": f"Bearer {token}"}
        )
        if res.status_code == 200:
            return res.json()
    except:
        pass
    return None

def select_car(request):
    token = request.session.get("token")
    username = request.session.get("username")
    
    if not token:
        return redirect("login")
    
    # Get the current user from database
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return redirect("login")
    
    # Get current car if exists
    current_car = None
    try:
        user_car = UserCar.objects.get(user=user)
        current_car = user_car.car
    except UserCar.DoesNotExist:
        pass
    
    # Handle POST request to save selected car
    if request.method == "POST":
        car_id = request.POST.get("car_id")
        if car_id:
            try:
                car = Car.objects.get(id=car_id)
                user_car, created = UserCar.objects.update_or_create(
                    user=user,
                    defaults={"car": car}
                )
                # If coming from profile, go back to profile
                next_page = request.GET.get('next', 'stations')
                return redirect(next_page)
            except Car.DoesNotExist:
                pass

    res = requests.get(
        f"{API_BASE}/auth/cars/",
        headers={"Authorization": f"Bearer {token}"}
    )

    if res.status_code == 200:
        cars = res.json()
    else:
        cars = []

    return render(request, "web/select_car.html", {
        "cars": cars,
        "current_car": current_car,
        "username": username
    })



def stations_view(request):
    token = request.session.get("token")
    username = request.session.get("username")
    
    if not token:
        return redirect("login")

    # Get the current user from database
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return redirect("login")

    # Get selected car
    selected_car = None
    try:
        user_car = UserCar.objects.get(user=user)
        selected_car = user_car.car
    except UserCar.DoesNotExist:
        return redirect("select_car")

    # Get filter parameters
    keyword = request.GET.get("q", "")
    charger_type = request.GET.get("charger_type", "")
    connector_type = request.GET.get("connector_type", "")
    min_power = request.GET.get("min_power", "")
    max_price = request.GET.get("max_price", "")
    user_lat = request.GET.get("lat", "")
    user_lon = request.GET.get("lon", "")
    radius = request.GET.get("radius", "50")  # Default 50 km radius

    # If no search query and no location, show message
    error_msg = None
    if not keyword and not user_lat:
        error_msg = "Please search for a city or use your location to find stations."

    # ── When a keyword is given, try to geocode it as a location first ──
    # This lets users type a city/area name and see ALL stations near that place,
    # rather than only matching stations whose name contains the keyword.
    geocoded_lat = None
    geocoded_lon = None
    geocoded_display = None
    if keyword:
        try:
            nom_resp = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"format": "json", "q": keyword, "limit": 1},
                headers={"User-Agent": "Obturo EV Charging App"},
                timeout=5,
            )
            if nom_resp.status_code == 200:
                nom_data = nom_resp.json()
                if nom_data:
                    geocoded_lat = float(nom_data[0]["lat"])
                    geocoded_lon = float(nom_data[0]["lon"])
                    geocoded_display = nom_data[0].get("display_name", keyword)
        except Exception:
            pass

    # Decide the search centre:
    # • If the keyword was successfully geocoded → use that location, fetch all stations nearby
    # • Else fall back to user GPS location with name-based filtering
    if geocoded_lat is not None:
        # Location search: fetch ALL stations, filter by distance from geocoded point
        res = requests.get(f"{API_BASE}/api/map/search/", params={"q": ""})
        stations = res.json() if res.status_code == 200 else []
        search_lat = geocoded_lat
        search_lon = geocoded_lon
        search_radius = float(radius)  # use whatever radius the user selected
        for station in stations:
            if "latitude" in station and "longitude" in station:
                station["distance"] = round(
                    get_distance(search_lat, search_lon,
                                 float(station["latitude"]), float(station["longitude"])), 2)
        stations = [s for s in stations if s.get("distance", 999) <= search_radius]
        stations = sorted(stations, key=lambda x: x.get("distance", 999))
        # Override the map centre so the template pans to the searched city
        user_lat = geocoded_lat
        user_lon = geocoded_lon
    else:
        # Station-name search (or no keyword) + optional GPS radius filter
        res = requests.get(
            f"{API_BASE}/api/map/search/",
            params={"q": keyword if keyword else ""},
        )
        stations = res.json() if res.status_code == 200 else []

        if user_lat and user_lon:
            try:
                user_lat = float(user_lat)
                user_lon = float(user_lon)
                radius_float = float(radius)
                for station in stations:
                    if "latitude" in station and "longitude" in station:
                        station["distance"] = round(
                            get_distance(user_lat, user_lon,
                                         float(station["latitude"]), float(station["longitude"])), 2)
                stations = [s for s in stations if s.get("distance", 999) <= radius_float]
                stations = sorted(stations, key=lambda x: x.get("distance", 999))
            except (ValueError, TypeError):
                pass
    
    # Apply other filters
    if charger_type:
        stations = [s for s in stations if s.get("charger_type", "DC") == charger_type]
    if connector_type:
        stations = [s for s in stations if s.get("connector_type", "CCS2") == connector_type]
    if min_power:
        try:
            min_power_val = float(min_power)
            stations = [s for s in stations if s.get("power_kw", 30) >= min_power_val]
        except:
            pass
    if max_price:
        try:
            max_price_val = float(max_price)
            stations = [s for s in stations if s.get("price_per_kwh", 18) <= max_price_val]
        except:
            pass

    # ── Real-time availability: use dataset value, minus any live active bookings ──
    now = dj_tz.now()
    active_bk = StationBooking.objects.filter(
        start_time__lte=now, end_time__gte=now, status='active'
    ).values('station_id').annotate(cnt=Count('id'))
    busy_map = {b['station_id']: b['cnt'] for b in active_bk}
    for s in stations:
        sid = s.get('id')
        total = s.get('total_slots') or 4
        # Use stored available_slots from the dataset as the base value.
        # If it's missing, default to total_slots.
        db_available = s.get('available_slots')
        if db_available is None:
            db_available = total
        live_busy = busy_map.get(sid, 0)
        # Subtract any real-time bookings that weren't reflected in the stored value.
        s['available_slots'] = max(0, db_available - live_busy)
        s['status'] = 'Available' if s['available_slots'] > 0 else 'Busy'

    # Get unique values for filters
    all_stations = res.json() if res.status_code == 200 else []
    charger_types = list(set([s.get("charger_type", "DC") for s in all_stations]))
    connector_types = list(set([s.get("connector_type", "CCS2") for s in all_stations]))
    
    # Debug info
    print(f"DEBUG: Final stations count: {len(stations)}")
    print(f"DEBUG: Error message: {error_msg}")
    print(f"DEBUG: Has location: {bool(user_lat and user_lon)}")
    
    # Build car data for the range filter feature
    car_range_km = 300
    car_battery_kwh = 40.0
    car_name = "Unknown Car"
    if selected_car:
        car_name = selected_car.name
        car_battery_kwh = selected_car.battery_capacity_kwh
        car_range_km = getattr(selected_car, 'wltp_range_km', 300)

    return render(request, "web/stations.html", {
        "stations": stations,
        "selected_car": selected_car,
        "charger_types": sorted(charger_types),
        "connector_types": sorted(connector_types),
        "current_filters": {
            "q": keyword,
            "charger_type": charger_type,
            "connector_type": connector_type,
            "min_power": min_power,
            "max_price": max_price,
            "lat": user_lat,
            "lon": user_lon,
            "radius": radius,
        },
        "has_location": bool(user_lat and user_lon),
        "error_msg": error_msg,
        "auth_token": token,
        # Range filter data
        "car_range_km": car_range_km,
        "car_battery_kwh": car_battery_kwh,
        "car_name": car_name,
    })


def station_detail_view(request):
    """Display detailed information for a specific station"""
    token = request.session.get("token")
    if not token:
        return redirect("login")
    
    return render(request, "web/station_detail.html", {
        "auth_token": token,
        "username": request.session.get("username")
    })


def analytics_view(request):
    token = request.session.get("token")
    username = request.session.get("username")
    
    if not token:
        return redirect("login")
    
    return render(request, "web/analytics.html", {
        "auth_token": token,
        "username": username
    })


def charging_session_view(request):
    """Display active charging session page"""
    token = request.session.get("token")
    if not token:
        return redirect("login")

    booking_id = request.GET.get("booking")
    booking_ctx = None
    if booking_id:
        try:
            bk = StationBooking.objects.select_related("station").get(
                id=booking_id, user__username=request.session.get("username")
            )
            booking_ctx = {
                "id": bk.id,
                "station_name": bk.station.name,
                "connector_type": bk.station.connector_type or "CCS2",
                "power_kw": bk.station.power_kw or 7.2,
                "price_per_kwh": bk.station.price_per_kwh or 12,
                "start_time": bk.start_time.strftime("%Y-%m-%d %H:%M"),
                "end_time": bk.end_time.strftime("%Y-%m-%d %H:%M"),
                "current_soc": 20,
                "target_soc": 80,
            }
        except StationBooking.DoesNotExist:
            pass

    return render(request, "web/charging_session.html", {
        "auth_token": token,
        "username": request.session.get("username"),
        "booking": booking_ctx or {},
    })


def signup_view(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        password_confirm = request.POST.get("password_confirm", "")

        def signup_error(msg):
            # Always return to login.html with the signup tab open
            return render(request, "web/login.html", {"error": msg, "signup_mode": True})

        # Validate inputs (password_confirm is optional — only check if provided)
        if not all([username, email, password]):
            return signup_error("All fields are required")

        if len(password) < 6:
            return signup_error("Password must be at least 6 characters")

        if password_confirm and password != password_confirm:
            return signup_error("Passwords do not match")

        if "@" not in email:
            return signup_error("Invalid email address")

        # --- create user directly (no internal HTTP round-trip) ---
        if User.objects.filter(username=username).exists():
            return signup_error("Username already taken")
        if User.objects.filter(email=email).exists():
            return signup_error("Email is already registered")

        try:
            user = User.objects.create_user(username=username, email=email, password=password)
        except Exception as e:
            return signup_error(f"Could not create account: {e}")

        # Auto-login immediately after creation
        refresh = RefreshToken.for_user(user)
        request.session["token"] = str(refresh.access_token)
        request.session["username"] = user.username
        user.backend = "django.contrib.auth.backends.ModelBackend"
        django_login(request, user)
        request.session.save()
        return redirect("home")

    return render(request, "web/login.html", {"signup_mode": True})


def logout_view(request):
    django_logout(request)
    request.session.flush()
    return redirect("login")


def dashboard_view(request):
    token = request.session.get("token")
    username = request.session.get("username")
    
    if not token:
        return redirect("login")

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return redirect("login")

    selected_car = None
    try:
        user_car = UserCar.objects.get(user=user)
        selected_car = user_car.car
    except UserCar.DoesNotExist:
        pass

    # Get bookings
    bookings_res = requests.get(
        f"{API_BASE}/api/bookings/my/",
        headers={"Authorization": f"Bearer {token}"}
    )
    bookings = bookings_res.json() if bookings_res.status_code == 200 else []

    # Get favorites
    favorites_res = requests.get(
        f"{API_BASE}/api/favourites/list/",
        headers={"Authorization": f"Bearer {token}"}
    )
    favorites = favorites_res.json() if favorites_res.status_code == 200 else []

    return render(request, "web/dashboard.html", {
        "selected_car": selected_car,
        "bookings_count": len(bookings),
        "favorites_count": len(favorites),
        "recent_bookings": bookings[:3],
        "username": username,
        "auth_token": token,
    })


def bookings_view(request):
    from datetime import datetime
    token = request.session.get("token")
    username = request.session.get("username")
    
    if not token:
        return redirect("login")

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return redirect("login")

    error_msg = None

    # Handle cancel booking POST request
    if request.method == "POST":
        booking_id = request.POST.get("booking_id")
        action = request.POST.get("action")
        
        if action == "cancel" and booking_id:
            cancel_res = requests.post(
                f"{API_BASE}/api/bookings/cancel/",
                json={"booking_id": int(booking_id)},
                headers={"Authorization": f"Bearer {token}"}
            )
            
            if cancel_res.status_code == 200:
                return redirect("bookings")  # Reload to show updated status
            else:
                error_msg = cancel_res.json().get("error", "Failed to cancel booking")
                print(f"Cancel error: {error_msg}")
    
    # Get bookings
    bookings_res = requests.get(
        f"{API_BASE}/api/bookings/my/",
        headers={"Authorization": f"Bearer {token}"}
    )
    bookings = bookings_res.json() if bookings_res.status_code == 200 else []
    
    # Categorize bookings by status and date
    bookings_upcoming = []
    bookings_past = []
    bookings_cancelled = []
    
    now = datetime.now()
    
    for booking in bookings:
        # Ensure booking has the required fields
        if not hasattr(booking, 'get'):
            booking = booking.__dict__ if hasattr(booking, '__dict__') else {}
        
        status = booking.get('status', 'upcoming').lower()
        
        # Parse end time to determine if booking is in the past
        try:
            if isinstance(booking.get('end_time'), str):
                end_time = datetime.fromisoformat(booking['end_time'].replace('Z', '+00:00'))
            else:
                end_time = now
        except:
            end_time = now
        
        # Categorize
        if status == 'cancelled':
            bookings_cancelled.append(booking)
        elif end_time > now or status in ['active', 'upcoming', 'confirmed']:
            bookings_upcoming.append(booking)
        else:
            bookings_past.append(booking)

    return render(request, "web/bookings.html", {
        "bookings": bookings,
        "bookings_upcoming": bookings_upcoming,
        "bookings_past": bookings_past,
        "bookings_cancelled": bookings_cancelled,
        "bookings_all": bookings,
        "username": username,
        "auth_token": token,
        "error_msg": error_msg,
    })


def profile_view(request):
    token = request.session.get("token")
    username = request.session.get("username")
    
    if not token:
        return redirect("login")

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return redirect("login")

    selected_car = None
    try:
        user_car = UserCar.objects.get(user=user)
        selected_car = user_car.car
    except UserCar.DoesNotExist:
        pass

    return render(request, "web/profile.html", {
        "user": user,
        "selected_car": selected_car,
        "username": username
    })


def favorites_view(request):
    token = request.session.get("token")
    username = request.session.get("username")
    
    if not token:
        return redirect("login")

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return redirect("login")

    # Get favorites
    favorites_res = requests.get(
        f"{API_BASE}/api/favourites/list/",
        headers={"Authorization": f"Bearer {token}"}
    )
    favorites = favorites_res.json() if favorites_res.status_code == 200 else []

    return render(request, "web/favorites.html", {
        "favorites": favorites,
        "username": username,
        "auth_token": token,
    })


def peer_chargers_view(request):
    token = request.session.get("token")
    username = request.session.get("username")
    
    if not token:
        return redirect("login")

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return redirect("login")

    # Get peer chargers nearby
    chargers_res = requests.get(
        f"{API_BASE}/api/p2p/chargers/nearby/",
        headers={"Authorization": f"Bearer {token}"}
    )
    chargers = chargers_res.json() if chargers_res.status_code == 200 else []

    return render(request, "web/peer_chargers.html", {
        "chargers": chargers,
        "username": username,
        "auth_token": token,
    })


def book_station_view(request, station_id):
    token = request.session.get("token")
    username = request.session.get("username")

    if not token:
        return redirect("login")

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return redirect("login")

    # Fetch station details so the template can display them
    station = {}
    try:
        station_res = requests.get(
            f"{API_BASE}/api/stations/{station_id}/detail/",
            headers={"Authorization": f"Bearer {token}"}
        )
        if station_res.status_code == 200:
            station = station_res.json()
    except Exception:
        pass

    if request.method == "POST":
        start_time = request.POST.get("start_time")
        end_time = request.POST.get("end_time")

        book_res = requests.post(
            f"{API_BASE}/api/book/",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "station_id": station_id,
                "start_time": start_time,
                "end_time": end_time,
            }
        )

        if book_res.status_code == 200:
            return redirect("bookings")
        else:
            error = book_res.json().get("error", "Booking failed")
            return render(request, "web/booking_slot.html", {
                "error": error,
                "station_id": station_id,
                "station": station,
                "auth_token": token,
            })

    return render(request, "web/booking_slot.html", {
        "station_id": station_id,
        "station": station,
        "auth_token": token,
    })


def ranking_view(request):
    """Smart station ranking with custom TOPSIS weights"""
    token = request.session.get("token")
    username = request.session.get("username")
    
    if not token:
        return redirect("login")

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        redirect("login")

    # Get selected car
    selected_car = None
    try:
        user_car = UserCar.objects.get(user=user)
        selected_car = user_car.car
    except UserCar.DoesNotExist:
        pass

    # Get location parameters
    user_lat = request.GET.get("lat", "")
    user_lon = request.GET.get("lon", "")
    radius = request.GET.get("radius", "50")

    return render(request, "web/ranking.html", {
        "selected_car": selected_car,
        "auth_token": token,
        "username": username,
        "current_filters": {
            "lat": user_lat,
            "lon": user_lon,
            "radius": radius,
        },
        "has_location": bool(user_lat and user_lon),
    })


def route_map_view(request):
    """Route map page: show route between source and destination with available stations along route"""
    token = request.session.get("token")
    username = request.session.get("username")

    user = None
    if token and username:
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            user = None

    # Render even for anonymous users so the planner loads instead of the login page
    return render(request, "web/route_map.html", {
        "user": user,
        "auth_token": token or "",
    })


def home_view(request):
    """Home page with all features overview"""
    token = request.session.get("token")
    username = request.session.get("username")
    
    if not token:
        return redirect("login")

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return redirect("login")
    
    # Check if user has selected a car
    try:
        UserCar.objects.get(user=user)
    except UserCar.DoesNotExist:
        # New user needs to select a car first
        return redirect("select_car")

    return render(request, "web/home.html", {
        "user": user,
        "auth_token": token,
    })


def geocode_view(request):
    """Proxy endpoint for geocoding to avoid CORS issues"""
    query = request.GET.get('q', '')
    if not query:
        return JsonResponse({'error': 'Query parameter required'}, status=400)
    
    try:
        import requests
        url = f"https://nominatim.openstreetmap.org/search?format=json&q={query}&limit=1"
        headers = {'User-Agent': 'Obturo EV Charging App'}
        response = requests.get(url, headers=headers, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 0:
                return JsonResponse({
                    'lat': float(data[0]['lat']),
                    'lon': float(data[0]['lon']),
                    'display_name': data[0]['display_name']
                })
            return JsonResponse({'error': 'No results found'}, status=404)
        return JsonResponse({'error': f'Geocoding service error: {response.status_code}'}, status=response.status_code)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def admin_dashboard_view(request):
    """Admin dashboard page"""
    from django.db.models import Count, Sum, Avg
    from django.utils import timezone
    from datetime import timedelta
    from stations.models import ChargingStation, Booking, StationRating, Waitlist
    
    token = request.session.get("token")
    username = request.session.get("username")
    
    if not token:
        return redirect("login")

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return redirect("login")
    
    # Check if user is staff/admin
    if not user.is_staff:
        return redirect("home")
    
    # Calculate analytics
    total_bookings = Booking.objects.count()
    active_bookings = Booking.objects.filter(status='active').count()
    completed_bookings = Booking.objects.filter(status='completed').count()
    cancelled_bookings = Booking.objects.filter(status='cancelled').count()
    
    # Recent bookings (last 7 days)
    week_ago = timezone.now() - timedelta(days=7)
    recent_bookings = Booking.objects.filter(created_at__gte=week_ago).count()
    
    # Station stats
    total_stations = ChargingStation.objects.count()
    available_stations = ChargingStation.objects.filter(status='Available').count()
    
    # Top rated stations
    top_stations = ChargingStation.objects.annotate(
        avg_rating=Avg('ratings__rating'),
        rating_count=Count('ratings')
    ).filter(rating_count__gt=0).order_by('-avg_rating')[:5]
    
    # Waitlist count
    total_waitlist = Waitlist.objects.count()
    
    return render(request, "web/admin_dashboard.html", {
        "username": username,
        "auth_token": token,
        "total_bookings": total_bookings,
        "active_bookings": active_bookings,
        "completed_bookings": completed_bookings,
        "cancelled_bookings": cancelled_bookings,
        "recent_bookings": recent_bookings,
        "total_stations": total_stations,
        "available_stations": available_stations,
        "top_stations": top_stations,
        "total_waitlist": total_waitlist,
    })
