"""
WebSocket consumers for real-time slot updates
"""
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import ChargingStation, Booking
from django.utils import timezone


class StationSlotsConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time charging station slot updates.
    
    Clients connect with: ws://<host>/ws/station/{station_id}/
    Server broadcasts slot updates to all connected clients for that station.
    """
    
    async def connect(self):
        """Handle WebSocket connection"""
        self.station_id = self.scope['url_route']['kwargs']['station_id']
        self.room_group_name = f'station_{self.station_id}'
        
        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        # Accept connection
        await self.accept()
        
        # Send initial station data
        station_data = await self.get_station_data()
        await self.send(text_data=json.dumps({
            'type': 'initial_data',
            'data': station_data
        }))
        
        print(f"[WebSocket] Client connected to station {self.station_id}")

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection"""
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
        print(f"[WebSocket] Client disconnected from station {self.station_id}")

    async def slots_update(self, event):
        """
        Receive message from room group - broadcast to WebSocket.
        This is triggered when slots change (booking created/cancelled)
        """
        await self.send(text_data=json.dumps({
            'type': 'slots_update',
            'data': event['data']
        }))

    @database_sync_to_async
    def get_station_data(self):
        """Fetch current station data including available slots"""
        try:
            station = ChargingStation.objects.get(id=self.station_id)
            
            # Count active bookings
            now = timezone.now()
            active_bookings = Booking.objects.filter(
                station=station,
                start_time__lte=now,
                end_time__gte=now,
                status='active'
            ).count()
            
            return {
                'id': station.id,
                'name': station.name,
                'available_slots': station.available_slots,
                'total_slots': station.total_slots,
                'active_bookings': active_bookings,
                'status': station.status,
                'price_per_kwh': float(station.price_per_kwh),
                'power_kw': float(station.power_kw),
            }
        except ChargingStation.DoesNotExist:
            return None
