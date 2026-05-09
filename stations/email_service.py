"""
Email service for sending booking confirmation emails
"""
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings


def send_booking_confirmation_email(user, booking, station):
    """
    Send booking confirmation email to user

    Args:
        user: User object
        booking: Booking object
        station: ChargingStation object
    """
    try:
        # ── Calculate duration and total cost ──────────────────────────
        duration_seconds = (booking.end_time - booking.start_time).total_seconds()
        duration_hours   = duration_seconds / 3600
        duration_h       = int(duration_hours)
        duration_m       = int((duration_hours - duration_h) * 60)
        duration_label   = f"{duration_h}h {duration_m}m" if duration_h else f"{duration_m}m"

        energy_kwh   = round(duration_hours * station.power_kw, 2)
        total_amount = round(energy_kwh * station.price_per_kwh, 2)

        user_name       = user.first_name or user.username
        booking_id      = booking.id
        station_name    = station.name
        station_address = station.address or "—"
        start_fmt       = booking.start_time.strftime("%d %b %Y, %I:%M %p")
        end_fmt         = booking.end_time.strftime("%d %b %Y, %I:%M %p")
        charger_type    = station.charger_type
        connector_type  = station.connector_type
        power_kw        = station.power_kw
        price_per_kwh   = station.price_per_kwh

        subject = f"Booking Confirmed – {station_name} | Obturo"

        html_message = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Booking Confirmation</title>
</head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:'Segoe UI',Arial,sans-serif;">

  <!-- Wrapper -->
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f4f8;padding:40px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#0040c1 0%,#0066ff 100%);border-radius:16px 16px 0 0;padding:36px 40px;text-align:center;">
            <p style="margin:0 0 6px 0;font-size:28px;font-weight:800;color:#ffffff;letter-spacing:1px;">&#9889; OBTURO</p>
            <p style="margin:0;font-size:14px;color:rgba(255,255,255,0.75);letter-spacing:2px;text-transform:uppercase;">EV Charging Platform</p>
          </td>
        </tr>

        <!-- Green confirmed banner -->
        <tr>
          <td style="background:#16a34a;padding:16px 40px;text-align:center;">
            <p style="margin:0;font-size:16px;font-weight:700;color:#ffffff;">&#10003;&nbsp; Booking Confirmed!</p>
          </td>
        </tr>

        <!-- Body card -->
        <tr>
          <td style="background:#ffffff;padding:36px 40px;">

            <p style="margin:0 0 20px 0;font-size:16px;color:#1e293b;">Hi <strong>{user_name}</strong>,</p>
            <p style="margin:0 0 28px 0;font-size:15px;color:#475569;line-height:1.6;">
              Your EV charging slot has been successfully booked. Find all the details below.
            </p>

            <!-- Booking ID pill -->
            <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:14px 20px;margin-bottom:28px;display:inline-block;width:100%;box-sizing:border-box;">
              <span style="font-size:13px;color:#3b82f6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Booking Reference</span>
              <p style="margin:4px 0 0 0;font-size:22px;font-weight:800;color:#1e40af;">#OBT-{booking_id:05d}</p>
            </div>

            <!-- Station info -->
            <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
              <tr>
                <td style="background:#f8fafc;border-radius:12px;padding:20px 24px;border-left:4px solid #0066ff;">
                  <p style="margin:0 0 4px 0;font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Charging Station</p>
                  <p style="margin:0 0 6px 0;font-size:18px;font-weight:700;color:#0f172a;">{station_name}</p>
                  <p style="margin:0;font-size:13px;color:#64748b;">&#128205; {station_address}</p>
                </td>
              </tr>
            </table>

            <!-- Time row -->
            <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
              <tr>
                <td width="48%" style="background:#f8fafc;border-radius:12px;padding:18px 20px;vertical-align:top;">
                  <p style="margin:0 0 4px 0;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Check-in</p>
                  <p style="margin:0;font-size:14px;font-weight:700;color:#0f172a;">{start_fmt}</p>
                </td>
                <td width="4%"></td>
                <td width="48%" style="background:#f8fafc;border-radius:12px;padding:18px 20px;vertical-align:top;">
                  <p style="margin:0 0 4px 0;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Check-out</p>
                  <p style="margin:0;font-size:14px;font-weight:700;color:#0f172a;">{end_fmt}</p>
                </td>
              </tr>
            </table>

            <!-- Specs row -->
            <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
              <tr>
                <td width="23%" style="background:#f8fafc;border-radius:10px;padding:14px 16px;text-align:center;vertical-align:top;">
                  <p style="margin:0 0 4px 0;font-size:20px;">&#128268;</p>
                  <p style="margin:0 0 2px 0;font-size:11px;color:#64748b;font-weight:600;">Type</p>
                  <p style="margin:0;font-size:13px;font-weight:700;color:#0f172a;">{charger_type}</p>
                </td>
                <td width="2%"></td>
                <td width="23%" style="background:#f8fafc;border-radius:10px;padding:14px 16px;text-align:center;vertical-align:top;">
                  <p style="margin:0 0 4px 0;font-size:20px;">&#128279;</p>
                  <p style="margin:0 0 2px 0;font-size:11px;color:#64748b;font-weight:600;">Connector</p>
                  <p style="margin:0;font-size:13px;font-weight:700;color:#0f172a;">{connector_type}</p>
                </td>
                <td width="2%"></td>
                <td width="23%" style="background:#f8fafc;border-radius:10px;padding:14px 16px;text-align:center;vertical-align:top;">
                  <p style="margin:0 0 4px 0;font-size:20px;">&#9889;</p>
                  <p style="margin:0 0 2px 0;font-size:11px;color:#64748b;font-weight:600;">Power</p>
                  <p style="margin:0;font-size:13px;font-weight:700;color:#0f172a;">{power_kw} kW</p>
                </td>
                <td width="2%"></td>
                <td width="25%" style="background:#f8fafc;border-radius:10px;padding:14px 16px;text-align:center;vertical-align:top;">
                  <p style="margin:0 0 4px 0;font-size:20px;">&#128336;</p>
                  <p style="margin:0 0 2px 0;font-size:11px;color:#64748b;font-weight:600;">Duration</p>
                  <p style="margin:0;font-size:13px;font-weight:700;color:#0f172a;">{duration_label}</p>
                </td>
              </tr>
            </table>

            <!-- Cost breakdown -->
            <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border-radius:12px;padding:20px 24px;margin-bottom:28px;">
              <tr>
                <td style="padding:0 0 12px 0;">
                  <p style="margin:0;font-size:13px;font-weight:700;color:#0f172a;text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid #e2e8f0;padding-bottom:12px;">Payment Summary</p>
                </td>
              </tr>
              <tr>
                <td style="padding:8px 0;">
                  <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                      <td style="font-size:14px;color:#475569;">Energy consumed</td>
                      <td align="right" style="font-size:14px;color:#475569;">{energy_kwh} kWh</td>
                    </tr>
                  </table>
                </td>
              </tr>
              <tr>
                <td style="padding:4px 0;">
                  <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                      <td style="font-size:14px;color:#475569;">Rate</td>
                      <td align="right" style="font-size:14px;color:#475569;">&#8377;{price_per_kwh}/kWh</td>
                    </tr>
                  </table>
                </td>
              </tr>
              <tr>
                <td style="padding-top:14px;border-top:2px solid #e2e8f0;">
                  <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                      <td style="font-size:17px;font-weight:800;color:#0f172a;">Total Payable</td>
                      <td align="right" style="font-size:22px;font-weight:800;color:#16a34a;">&#8377;{total_amount}</td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>

            <!-- Note -->
            <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
              <tr>
                <td style="background:#fffbeb;border:1px solid #fcd34d;border-radius:10px;padding:14px 18px;">
                  <p style="margin:0;font-size:13px;color:#92400e;line-height:1.6;">
                    <strong>&#9888; Reminder:</strong> Please arrive <strong>10 minutes early</strong>. 
                    Cancellations or changes must be made at least <strong>2 hours</strong> before check-in.
                  </p>
                </td>
              </tr>
            </table>

            <!-- CTA button -->
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td align="center">
                  <a href="{booking_url}"
                     style="display:inline-block;padding:14px 40px;background:linear-gradient(135deg,#0040c1,#0066ff);color:#ffffff;text-decoration:none;border-radius:10px;font-size:15px;font-weight:700;letter-spacing:0.5px;">
                    View My Bookings &#8594;
                  </a>
                </td>
              </tr>
            </table>

          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#1e293b;border-radius:0 0 16px 16px;padding:24px 40px;text-align:center;">
            <p style="margin:0 0 6px 0;font-size:15px;font-weight:700;color:#ffffff;">&#9889; Obturo</p>
            <p style="margin:0;font-size:12px;color:#94a3b8;">This is an automated message — please do not reply.<br/>&copy; 2026 Obturo. All rights reserved.</p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>

</body>
</html>"""

        booking_url = f"{getattr(settings, 'API_BASE', 'http://127.0.0.1:8000').rstrip('/')}/bookings/"

        plain_message = f"""Booking Confirmed – Obturo
================================
Hi {user_name},

Your EV charging slot has been successfully booked.

Booking Reference : #OBT-{booking_id:05d}
Station           : {station_name}
Address           : {station_address}
Check-in          : {start_fmt}
Check-out         : {end_fmt}
Duration          : {duration_label}

Charger Type      : {charger_type}
Connector         : {connector_type}
Power             : {power_kw} kW

--- Payment Summary ---
Energy            : {energy_kwh} kWh
Rate              : Rs.{price_per_kwh}/kWh
Total Payable     : Rs.{total_amount}

Please arrive 10 minutes early. Cancellations must be made at least 2 hours before check-in.

View bookings: {booking_url}

Thank you for using Obturo!
"""

        send_mail(
            subject=subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )

        print(f"✅ Booking confirmation email sent to {user.email}")
        return True

    except Exception as e:
        print(f"Error sending booking confirmation email: {e}")
        return False


def send_waitlist_notification_email(user, station, position):
    """
    Send waitlist notification email to user
    
    Args:
        user: User object
        station: ChargingStation object
        position: Waitlist position
    """
    try:
        subject = f"⏳ You've Been Added to Waitlist - {station.name}"
        
        context = {
            'user_name': user.first_name or user.username,
            'station_name': station.name,
            'station_address': station.address,
            'position': position,
            'charger_type': station.charger_type,
        }
        
        html_message = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #ff9800;">⏳ Added to Waitlist</h2>
                    <p>Hi {context['user_name']},</p>
                    <p>The station <strong>{context['station_name']}</strong> is currently full. You have been added to the waitlist at <strong>position {context['position']}</strong>.</p>
                    <p>We will notify you via push notification when a slot becomes available.</p>
                </div>
            </body>
        </html>
        """
        
        plain_message = f"""
You've been added to the waitlist for {context['station_name']}
Position: {context['position']}

We will notify you when a slot becomes available.
        """
        
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
        
        return True
    except Exception as e:
        print(f"Error sending waitlist notification email: {e}")
        return False
