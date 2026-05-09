<div align="center">
  <h1>🔋 Obturo EV Charging Platform</h1>
  <p>An intelligent, full-stack Electric Vehicle charging station booking and management system.</p>

  <!-- Badges -->
  <p>
    <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python Version" />
    <img src="https://img.shields.io/badge/Django-6.0-092E20.svg" alt="Django" />
    <img src="https://img.shields.io/badge/Django%20REST-3.16-red.svg" alt="DRF" />
    <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License" />
  </p>
</div>

---

## 📖 Overview

The **Obturo EV Charging Platform** is a complete end-to-end solution for managing electric vehicle charging. It includes a **robust backend REST API** and a **dynamic Django-based Website**. The platform facilitates station exploration, smart route planning, real-time booking, peer-to-peer (P2P) charging, and waitlist management. 

Powered by the TOPSIS multi-criteria decision algorithm, it provides users with the smartest charging recommendations based on distance, price, and real-time slot availability.

## ✨ Key Features

### 🌐 Django Web Interface
- **Explore Stations & Route Planner**: Interactive web pages to search for charging stations, plan routes, and view station details.
- **Admin Analytics Dashboard**: A dedicated web-based panel for administrators to monitor total bookings, active sessions, station health, and user metrics in real time.
- **Interactive Station Pages**: Beautifully rendered Django templates where users can submit reviews, check helpfulness ratings, and join waitlists.

### ⚙️ Backend & APIs
- **🏆 Smart Station Ranking (TOPSIS)**: Ranks nearby charging stations based on an advanced algorithm weighing price, distance, power output, and current availability.
- **⚡ Real-time Slot Availability**: Uses Django Channels (WebSockets) to broadcast live updates on charging slot availability directly to the web interface and mobile clients.
- **⏳ Smart Waitlist System**: A fully automated queue system for full stations. Users are placed in a queue and automatically promoted and notified when slots free up.
- **🤝 P2P Charger Sharing**: Empowers private charger owners to list their hardware on the platform for other EV drivers to book.
- **🔔 Push Notifications**: Integrated with Firebase Cloud Messaging (FCM) to deliver instant alerts for booking confirmations, waitlist promotions, and session reminders.
- **⚖️ Penalty System**: Ensures fair usage by tracking no-shows and late cancellations, temporarily blocking users who abuse the booking system.

## 🛠️ Technology Stack

| Component | Technology |
| --- | --- |
| **Full-Stack Framework** | Django 6.0.1 (Backend & Web Templates) |
| **API Framework** | Django REST Framework 3.16.1 |
| **Database** | SQLite (Development) / PostgreSQL (Production) |
| **Real-time Engine** | Django Channels 4.3.2 |
| **Notifications** | Firebase Admin SDK 7.1.0 |
| **Background Tasks** | APScheduler 3.11.2 |
| **Algorithms** | NumPy 2.4.2 (TOPSIS calculation) |

## 🚀 Getting Started

### Prerequisites

- Python 3.10 or higher
- `pip` package manager
- `virtualenv` (recommended)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-username/obturo-backend.git
   cd obturo_backend
   ```

2. **Create and activate a virtual environment**
   ```bash
   python -m venv .venv
   
   # On Windows:
   .venv\Scripts\activate
   # On macOS/Linux:
   source .venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run Database Migrations**
   ```bash
   python manage.py migrate
   ```

5. **Load Sample Data (Optional)**
   *Populate the database with dummy users, stations, and EV profiles.*
   ```bash
   python create_sample_data.py
   python populate_station_data.py
   ```

6. **Start the Development Server**
   ```bash
   python manage.py runserver
   ```
   The website and API will be accessible at `http://127.0.0.1:8000/`.

## 📡 Core API Endpoints

While the project includes a full web interface, it also exposes APIs for mobile application integration:

### 🔐 Authentication & Users
- `POST /api/accounts/signup/` — Register a new account
- `POST /api/accounts/login/` — Authenticate and receive JWT tokens
- `GET /api/accounts/smart-car/` — Get smart AC/DC charging recommendations

### 🔌 Stations & Bookings
- `GET /api/stations/` — Retrieve all nearby stations
- `POST /api/stations/book/` — Create a new charging slot booking
- `POST /api/stations/cancel/` — Cancel an active booking
- `POST /api/stations/topsis/` — Retrieve stations ranked via TOPSIS

### ⏱️ Waitlist
- `POST /api/waitlist/join/` — Join the waitlist queue for a full station
- `GET /api/waitlist/position/` — Check your current position in the queue
- `POST /api/waitlist/leave/` — Remove yourself from the waitlist

### 🏠 P2P Charging
- `GET /api/stations/peer/` — List community-shared P2P chargers
- `POST /api/stations/peer/book/` — Book a community charger

## 🤝 Contributing

Contributions are welcome! Please follow these steps:
1. Fork the project.
2. Create your feature branch (`git checkout -b feature/AmazingFeature`).
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`).
4. Push to the branch (`git push origin feature/AmazingFeature`).
5. Open a Pull Request.

## 📄 License

This project is licensed under the MIT License.
 working demo website link: https://obturoev.pythonanywhere.com/
 
