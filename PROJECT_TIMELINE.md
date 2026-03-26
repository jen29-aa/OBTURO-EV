# Obturo Backend — Project Creation Timeline

This document records the exact date every major feature, file, and model was first created, based on the project's git commit history.

---

## Commit 1 — December 29, 2025
**"Initial commit"**

This commit established the entire backend foundation — the Django project, both core apps, all initial models, the TOPSIS algorithm, the full REST API, the waitlist system, and the background scheduler.

### Django Project Setup
| Item | Description |
|------|-------------|
| `manage.py` | Django management entry point |
| `obturo_backend/settings.py` | Project settings (database, auth, installed apps) |
| `obturo_backend/urls.py` | Root URL configuration |
| `obturo_backend/asgi.py` | ASGI entry point |
| `obturo_backend/wsgi.py` | WSGI entry point |
| `.gitignore` | Git ignore rules |
| `README.md` | Project readme |

### accounts App
| Item | Description |
|------|-------------|
| `accounts/models.py` | `Car` model (EV catalogue), `UserCar` model (user ↔ car link), `DeviceToken` model (FCM tokens) |
| `accounts/views.py` | `SignupView`, `LoginView` (JWT), `CarListView`, `SelectCarView`, `SmartCarDetailsView`, `save_device_token` |
| `accounts/urls.py` | URL routes for all accounts endpoints |
| `accounts/migrations/0001_initial.py` | Initial migration — creates Car and UserCar tables |
| `accounts/migrations/0002_devicetoken.py` | Adds DeviceToken table |
| `accounts/load_cars.py` | Management script to seed the Car catalogue |

### stations App — Models
| Model | Description |
|-------|-------------|
| `ChargingStation` | Core station entity — location, charger type, slots, pricing, status |
| `Booking` | Time-slot reservation (active / completed / cancelled) |
| `FavouriteStation` | User ↔ station saved favourites |
| `PeerCharger` | Private home charger listed by an owner |
| `PeerBooking` | Booking request against a P2P charger (pending / approved / rejected) |
| `StationRating` | 1–5 star rating + text review per user per station |
| `UserPenalty` | No-show / late-cancel point tracking with temporary booking block |
| `Waitlist` | Smart queue — position-ordered entries for full stations |
| `stations/migrations/0001_initial.py` | Creates all above tables |
| `stations/migrations/0002_userpenalty_blocked_until.py` | Adds `blocked_until` field to UserPenalty |

### stations App — Core Logic
| File | Description |
|------|-------------|
| `stations/topsis.py` | TOPSIS multi-criteria decision algorithm (normalise → weight → ideal best/worst → score) |
| `stations/waitlist_service.py` | `reorder_waitlist`, `estimate_wait_time`, `promote_waitlist_for_station`, `get_waitlist_info` |
| `stations/scheduler.py` | APScheduler — 1-minute jobs for booking reminders, auto-completion, no-show penalties, slot freeing |
| `stations/serializers.py` | `ChargingStationSerializer`, `BookingSerializer`, `UserProfileSerializer`, `FavouriteStationSerializer`, `PeerChargerSerializer`, `PeerBookingSerializer` |

### stations App — REST API (views.py + urls.py)
| Endpoint | Description |
|----------|-------------|
| `GET /api/stations/all/` | All stations |
| `GET /api/stations/nearby/` | Nearby stations with Haversine distance |
| `POST /api/stations/book/` | Create booking (auto-waitlist if full) |
| `POST /api/stations/cancel/` | Cancel booking (late-cancel penalty + immediate waitlist promotion) |
| `GET /api/stations/my-bookings/` | User booking history |
| `POST /api/stations/waitlist/join/` | Join station waitlist |
| `GET /api/stations/waitlist/position/` | Waitlist position + ETA |
| `POST /api/stations/waitlist/leave/` | Leave waitlist |
| `POST /api/stations/topsis/` | TOPSIS-ranked station recommendations with user-defined weights |
| `GET /api/stations/smart/` | Car-aware smart filtered stations (DC vs AC priority) |
| `POST /api/stations/rate/` | Submit or update a station rating |
| `GET /api/stations/rating/<id>/` | Get station average rating and reviews |
| `POST /api/stations/favourites/toggle/` | Favourite / unfavourite a station |
| `GET /api/stations/favourites/` | User's saved favourites |
| `GET /api/stations/map/` | Lightweight map marker data |
| `POST /api/stations/route/` | Stations along a navigation route polyline |
| `POST /api/stations/best-stops/` | Top-scored stops along a route |
| `GET /api/stations/search/` | Text search with TOPSIS scoring |
| `POST /api/stations/p2p/create/` | List a private charger |
| `GET /api/stations/p2p/nearby/` | Nearby private chargers |
| `POST /api/stations/p2p/book/` | Request a P2P booking |
| `POST /api/stations/p2p/approve/` | Owner approve or reject a P2P booking |
| `GET /api/stations/p2p/my-bookings/` | Renter's P2P booking history |
| `GET /api/stations/p2p/requests/` | Owner's pending P2P requests |

### Utility Scripts
| File | Description |
|------|-------------|
| `import_stations.py` | Script to bulk-import station data |
| `randomize_stations.py` | Script to randomise station slot counts for testing |

---

## Commit 2 — January 29, 2026
**"commit please"**

This commit added real-time WebSocket support, push notifications, email confirmations, the recently-viewed feature, a full HTML/CSS/JS web front-end, and a range of utility/admin tools.

### Real-Time WebSocket (Django Channels)
| Item | Description |
|------|-------------|
| `stations/consumers.py` | `StationSlotsConsumer` — WebSocket consumer that broadcasts live slot updates to all connected clients |
| `stations/routing.py` | Channels URL routing — maps `ws/station/<id>/` to the consumer |
| `obturo_backend/asgi.py` | Updated to mount Channels alongside Django |

### Push Notifications
| Item | Description |
|------|-------------|
| `stations/firebase.py` | Firebase Admin SDK integration — `send_push_notification(token, title, body)` |

### Email Service
| Item | Description |
|------|-------------|
| `stations/email_service.py` | `send_booking_confirmation_email` — HTML booking confirmation email |
| | `send_waitlist_notification_email` — HTML waitlist position email |

### New Model
| Item | Description |
|------|-------------|
| `RecentlyViewedStation` | Tracks the last 10 stations viewed by each user |
| `stations/migrations/0003_...` | Adds `description`, `email`, `phone_number`, `image_url`, `operating_hours`, `facilities`, `has_*` boolean fields to `ChargingStation` |
| `stations/migrations/0004_recentlyviewedstation.py` | Creates RecentlyViewedStation table |

### New API Endpoints Added
| Endpoint | Description |
|----------|-------------|
| `POST /api/stations/view/` | Track a station page view (recently viewed) |
| `GET /api/stations/recently-viewed/` | Get user's 10 most recently viewed stations |
| `GET /api/stations/<id>/` | Full station detail with facilities, ratings, contact info |
| `GET /api/stations/active-bookings/` | Active (not yet ended) bookings for sidebar widget |
| `GET /api/stations/booking/<id>/` | Single booking detail |
| `POST /api/accounts/device-token/` | Save FCM device token |
| `POST /api/stations/device-token/` | Save FCM device token (stations namespace) |

### Admin Panel
| Item | Description |
|------|-------------|
| `stations/admin.py` | Full Django admin registrations for all models with list filters, search, and display fields |

### Web Front-End (web app — all new)
| Template | Description |
|----------|-------------|
| `web/templates/web/home.html` | Landing page with map, search, and station cards |
| `web/templates/web/dashboard.html` | User dashboard with booking summary |
| `web/templates/web/stations.html` | Station list with filters and map view |
| `web/templates/web/station_detail.html` | Full station detail page |
| `web/templates/web/bookings.html` | User booking history page |
| `web/templates/web/charging_session.html` | Live charging session tracker |
| `web/templates/web/favorites.html` | Saved favourite stations page |
| `web/templates/web/peer_chargers.html` | P2P charger listing and booking page |
| `web/templates/web/ranking.html` | TOPSIS-ranked station comparison page |
| `web/templates/web/route_map.html` | Route planning with stations along the way |
| `web/templates/web/analytics.html` | Usage analytics charts |
| `web/templates/web/admin_dashboard.html` | Admin management dashboard |
| `web/templates/web/profile.html` | User profile and settings page |
| `web/templates/web/select_car.html` | EV model selection page |
| `web/templates/web/login.html` | Login page |
| `web/templates/web/signup.html` | Registration page |
| `web/templates/web/navbar.html` | Shared navigation bar |
| `web/templates/web/includes/active_bookings_sidebar.html` | Sidebar widget showing current bookings |
| `web/templates/web/includes/charging_widget.html` | Live charging progress widget |
| `web/templates/web/includes/sidebar_nav.html` | Sidebar navigation component |
| `web/views.py` | All web view functions serving the HTML templates |
| `web/urls.py` | URL routes for all web pages |

### Utility Scripts
| File | Description |
|------|-------------|
| `create_sample_data.py` | Seeds the database with sample stations, bookings, and users |
| `populate_station_data.py` | Populates detailed station data (images, descriptions, facilities) |
| `fix_penalty.py` | One-off script to repair penalty records |
| `make_admin.py` | Script to promote a user to admin/staff |
| `test_api.py` | API test suite for core endpoints |

---

## Commit 3 — March 12, 2026
**"new commit website changes"**

This commit added crowd-sourced station reports, admin analytics APIs, external service proxies (geocoding, routing, map tiles), new booking flow pages, and a major visual overhaul of all web templates.

### New Model — StationReport
| Item | Description |
|------|-------------|
| `StationReport` | Crowd-sourced real-time reports — types: broken / queue / closed / offline / clean — with 2-hour auto-expiry and upvote count |
| `stations/migrations/0005_alter_stationrating_options_and_more.py` | Updates StationRating ordering and adds `helpful_count` field |
| `stations/migrations/0006_waitlist_expires_at.py` | Adds `expires_at` field to Waitlist (entries auto-expire after 30 minutes) |
| `stations/migrations/0007_stationreport.py` | Creates StationReport table |

### New Model Field
| Item | Description |
|------|-------------|
| `Car.wltp_range_km` | WLTP range in km added to Car model |
| `accounts/migrations/0003_car_wltp_range_km.py` | Migration for the new field |

### New API Endpoints
| Endpoint | Description |
|----------|-------------|
| `POST /api/stations/report/` | Submit a crowd-sourced station problem report |
| `GET /api/stations/reports/<id>/` | Get all active (non-expired) reports for a station |
| `GET /api/stations/reports/all/` | All active reports across all stations (for map overlay) |
| `POST /api/stations/report/upvote/` | Upvote a report to increase its credibility |
| `GET /api/stations/geocode/` | Address search proxy — forwards to Nominatim (OpenStreetMap), India only |
| `GET /api/stations/route/osrm/` | Turn-by-turn routing proxy — forwards to OSRM |
| `GET /api/stations/tiles/<z>/<x>/<y>/` | Map tile proxy — serves CartoCDN tiles through the backend |
| `GET /api/stations/admin/stats/` | Admin dashboard KPIs (users, stations, bookings, revenue, utilisation) |
| `GET /api/stations/admin/revenue/` | Detailed daily and monthly revenue analytics |
| `GET /api/stations/admin/users/` | Paginated user management list with penalty and booking data |
| `GET /api/stations/admin/bookings/` | Booking analytics — status distribution, cancellation rate, peak hours |
| `GET /api/stations/admin/stations/` | Station management list with utilisation and booking counts |

### New Web Templates
| Template | Description |
|----------|-------------|
| `web/templates/web/base.html` | Base layout template extended by all pages |
| `web/templates/web/book.html` | Single-station booking flow page |
| `web/templates/web/booking_slot.html` | Time-slot selection UI |
| `web/templates/web/booking_confirmation_partial.html` | Booking confirmation partial/modal |
| `web/templates/web/includes/_navbar.html` | Redesigned top navigation bar component |

### Major Web Template Revamps
| Template | What Changed |
|----------|-------------|
| `stations.html` | Full redesign — improved filters, map integration, TOPSIS score badges |
| `station_detail.html` | Added crowd reports section, live slot WebSocket widget, enhanced facilities display |
| `ranking.html` | Rebuilt TOPSIS ranking UI with weight sliders and comparison table |
| `route_map.html` | Added OSRM route integration, stations-along-route overlay, best stops feature |
| `home.html` | New hero section, recently viewed, smart recommendations |
| `dashboard.html` | Active bookings widget, penalty status, car details panel |
| `admin_dashboard.html` | Revenue charts, user table, station utilisation — connected to new analytics APIs |
| `analytics.html` | Detailed charts for bookings, revenue, peak hours |
| `bookings.html` | Redesigned booking history with status filters |
| `charging_session.html` | Improved live session UI |
| `favorites.html` | Card-based favourites layout |
| `peer_chargers.html` | Improved P2P listing and approval UI |
| `profile.html` | Updated profile with car info and penalty status |
| `login.html` / `signup.html` | Visual refresh |
| `select_car.html` | Updated car picker with range and specs display |
| `active_bookings_sidebar.html` | Real-time WebSocket integration for live slot count |
| `sidebar_nav.html` | Restructured navigation with icons |

### Planning Documents Added
| File | Description |
|------|-------------|
| `FEATURE_ROADMAP.md` | Planned future features and priorities |
| `IMPLEMENTATION_SUMMARY.md` | Summary of all implemented features |
| `JOURNEY_TRACKING_FEATURES.md` | Notes on journey and route tracking features |
| `NEXT_STEPS.md` | Immediate next development steps |
| `PAYMENT_INTEGRATION.md` | Payment gateway integration plan |
| `PROJECT_STATUS.md` | Current project status overview |
| `TODO_REMAINING.md` | Remaining tasks and open issues |
| `CODE_DOCUMENTATION.md` | Full technical documentation of all code (created March 12, 2026) |

---

## Quick Date Reference

| Feature | Date Created |
|---------|-------------|
| Django project & app scaffolding | Dec 29, 2025 |
| User authentication (signup, login, JWT) | Dec 29, 2025 |
| Car model & EV selection | Dec 29, 2025 |
| FCM DeviceToken model | Dec 29, 2025 |
| ChargingStation model | Dec 29, 2025 |
| Booking model & booking API | Dec 29, 2025 |
| Waitlist system (model + service) | Dec 29, 2025 |
| P2P charger sharing (PeerCharger + PeerBooking) | Dec 29, 2025 |
| Station ratings & reviews | Dec 29, 2025 |
| User penalty system (no-show / late-cancel) | Dec 29, 2025 |
| TOPSIS algorithm | Dec 29, 2025 |
| APScheduler background jobs | Dec 29, 2025 |
| Favourite stations | Dec 29, 2025 |
| Map & route APIs | Dec 29, 2025 |
| Smart car-based station filtering | Dec 29, 2025 |
| Firebase push notifications | Jan 29, 2026 |
| Email service (booking & waitlist emails) | Jan 29, 2026 |
| Django Channels WebSocket (real-time slots) | Jan 29, 2026 |
| Recently viewed stations | Jan 29, 2026 |
| Station detail fields (facilities, description, images) | Jan 29, 2026 |
| Full web front-end (all HTML templates) | Jan 29, 2026 |
| Admin panel registrations | Jan 29, 2026 |
| Active bookings sidebar | Jan 29, 2026 |
| StationReport (crowd-sourced reports) | Mar 12, 2026 |
| Waitlist expiry (`expires_at` field) | Mar 12, 2026 |
| Car WLTP range field | Mar 12, 2026 |
| Geocode proxy (Nominatim) | Mar 12, 2026 |
| Routing proxy (OSRM) | Mar 12, 2026 |
| Map tile proxy (CartoCDN) | Mar 12, 2026 |
| Admin analytics APIs (revenue, users, bookings, stations) | Mar 12, 2026 |
| Report upvoting | Mar 12, 2026 |
| Web template base layout (`base.html`) | Mar 12, 2026 |
| Booking slot / booking flow pages | Mar 12, 2026 |
| Full web template redesign | Mar 12, 2026 |
