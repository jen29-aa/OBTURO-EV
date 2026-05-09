from django.apps import AppConfig
import os

class WebConfig(AppConfig):
    name = 'web'
    path = os.path.dirname(os.path.abspath(__file__))
