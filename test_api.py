#!/usr/bin/env python3
"""
Comprehensive API Testing Suite for Obturo Backend
Tests all critical endpoints and validates HTTP status codes
"""

import os
import requests
import json
import sys
from datetime import datetime, timedelta

# Configuration
BASE_URL = f"{os.getenv('API_BASE', 'http://localhost:8000').rstrip('/')}/api"
TIMEOUT = 10

class APITester:
    """Handles all API testing operations"""
    
    def __init__(self):
        self.token = None
        self.user_id = None
        self.station_id = 1
        self.booking_id = None
        self.session = requests.Session()
        self.results = {
            'passed': [],
            'failed': [],
            'errors': []
        }
    
    def test(self, method, endpoint, data=None, expected_status=200, description=""):
        """Execute a single API test"""
        url = f"{BASE_URL}{endpoint}"
        headers = {}
        
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        
        headers['Content-Type'] = 'application/json'
        
        try:
            if method.upper() == 'GET':
                response = self.session.get(url, headers=headers, timeout=TIMEOUT)
            elif method.upper() == 'POST':
                response = self.session.post(url, json=data, headers=headers, timeout=TIMEOUT)
            elif method.upper() == 'PUT':
                response = self.session.put(url, json=data, headers=headers, timeout=TIMEOUT)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            # Check status code
            if response.status_code == expected_status:
                self.results['passed'].append({
                    'endpoint': f"{method} {endpoint}",
                    'description': description,
                    'status': response.status_code
                })
                print(f"✓ {method:4} {endpoint:40} → {response.status_code}")
                return response
            else:
                self.results['failed'].append({
                    'endpoint': f"{method} {endpoint}",
                    'description': description,
                    'status': f"Expected {expected_status}, got {response.status_code}"
                })
                print(f"✗ {method:4} {endpoint:40} → {response.status_code} (expected {expected_status})")
                return None
        
        except requests.exceptions.Timeout:
            self.results['errors'].append({
                'endpoint': f"{method} {endpoint}",
                'description': description,
                'error': "Timeout"
            })
            print(f"⚠ {method:4} {endpoint:40} → TIMEOUT")
            return None
        
        except requests.exceptions.ConnectionError:
            self.results['errors'].append({
                'endpoint': f"{method} {endpoint}",
                'description': description,
                'error': "Connection Error"
            })
            print(f"⚠ {method:4} {endpoint:40} → CONNECTION ERROR")
            return None
        
        except Exception as e:
            self.results['errors'].append({
                'endpoint': f"{method} {endpoint}",
                'description': description,
                'error': str(e)
            })
            print(f"⚠ {method:4} {endpoint:40} → ERROR: {str(e)[:50]}")
            return None
    
    def print_summary(self):
        """Print test results summary"""
        passed = len(self.results['passed'])
        failed = len(self.results['failed'])
        errors = len(self.results['errors'])
        total = passed + failed + errors
        
        print("\n" + "=" * 80)
        print("TEST RESULTS SUMMARY")
        print("=" * 80)
        print(f"Total Tests: {total}")
        print(f"✓ Passed: {passed}")
        print(f"✗ Failed: {failed}")
        print(f"⚠ Errors: {errors}")
        
        if self.results['failed']:
            print("\n❌ FAILED ENDPOINTS:")
            for test in self.results['failed']:
                print(f"   • {test['endpoint']}: {test['status']}")
        
        if self.results['errors']:
            print("\n⚠️  ERROR ENDPOINTS:")
            for test in self.results['errors']:
                print(f"   • {test['endpoint']}: {test['error']}")
        
        print("\n" + "=" * 80)
        return passed == total


def run_tests():
    """Run all API tests"""
    tester = APITester()
    
    print("=" * 80)
    print("OBTURO API TEST SUITE")
    print("=" * 80)
    print(f"Base URL: {BASE_URL}\n")
    
    # ==================== STATION ENDPOINTS ====================
    print("\n📍 STATION ENDPOINTS:")
    print("-" * 80)
    
    tester.test('GET', '/stations/', expected_status=200, description="List all stations")
    tester.test('GET', '/stations/nearby/?lat=28.5355&lng=77.3910&radius=10', expected_status=200, 
                description="Get nearby stations")
    tester.test('GET', '/stations/1/detail/', expected_status=200, 
                description="Get station detail")
    tester.test('GET', '/stations/1/rating/', expected_status=200, 
                description="Get station ratings/reviews")
    
    # ==================== BOOKING ENDPOINTS ====================
    print("\n📅 BOOKING ENDPOINTS:")
    print("-" * 80)
    
    # Unauthenticated access should return 401
    booking_data = {
        "station_id": 1,
        "start_time": (datetime.now() + timedelta(hours=1)).isoformat(),
        "end_time": (datetime.now() + timedelta(hours=2)).isoformat()
    }
    
    tester.test('POST', '/book/', data=booking_data, expected_status=401, 
                description="Create booking (unauthenticated - should fail)")
    tester.test('POST', '/bookings/create/', data=booking_data, expected_status=401, 
                description="Create booking alternate endpoint (unauthenticated - should fail)")
    
    tester.test('GET', '/bookings/my/', expected_status=401, 
                description="List user bookings (unauthenticated - should fail)")
    tester.test('GET', '/bookings/1/', expected_status=401, 
                description="Get booking detail (unauthenticated - should fail)")
    
    # ==================== RATING ENDPOINTS ====================
    print("\n⭐ RATING ENDPOINTS:")
    print("-" * 80)
    
    rating_data = {
        "station_id": 1,
        "rating": 4,
        "review": "Great charging station with fast service"
    }
    
    tester.test('POST', '/stations/rate/', data=rating_data, expected_status=401, 
                description="Submit rating (unauthenticated - should fail)")
    
    tester.test('GET', '/stations/1/rating/', expected_status=200, 
                description="Get station ratings (public endpoint)")
    
    # ==================== WAITLIST ENDPOINTS ====================
    print("\n⏳ WAITLIST ENDPOINTS:")
    print("-" * 80)
    
    waitlist_data = {"station_id": 1}
    
    tester.test('POST', '/waitlist/join/', data=waitlist_data, expected_status=401, 
                description="Join waitlist (unauthenticated - should fail)")
    
    tester.test('GET', '/waitlist/position/?station_id=1', expected_status=401, 
                description="Get waitlist position (unauthenticated - should fail)")
    
    tester.test('POST', '/waitlist/leave/', data=waitlist_data, expected_status=401, 
                description="Leave waitlist (unauthenticated - should fail)")
    
    # ==================== ADMIN ENDPOINTS ====================
    print("\n📊 ADMIN ENDPOINTS:")
    print("-" * 80)
    
    tester.test('GET', '/admin/dashboard/stats/', expected_status=401, 
                description="Admin dashboard stats (unauthenticated - should fail)")
    
    tester.test('GET', '/admin/revenue/', expected_status=401, 
                description="Admin revenue analytics (unauthenticated - should fail)")
    
    tester.test('GET', '/admin/users/', expected_status=401, 
                description="Admin user management (unauthenticated - should fail)")
    
    tester.test('GET', '/admin/bookings/analytics/', expected_status=401, 
                description="Admin booking analytics (unauthenticated - should fail)")
    
    tester.test('GET', '/admin/stations/', expected_status=401, 
                description="Admin station management (unauthenticated - should fail)")
    
    # ==================== PRINT SUMMARY ====================
    all_passed = tester.print_summary()
    
    return 0 if all_passed else 1


if __name__ == '__main__':
    try:
        exit_code = run_tests()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  Testing interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
