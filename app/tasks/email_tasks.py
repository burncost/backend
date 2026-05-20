from app.core.celery_app import celery_app
from app.utils.email import send_email
import logging

logger = logging.getLogger(__name__)

###  Send email verification
@celery_app.task(name="send_verification_email")
def send_verification_email_task(email: str, verification_link: str):
    try:
        subject = "Verify Your Email Address"
        body = f"""
        <html>
            <body>
                <h2>Welcome to Building Materials Platform</h2>
                <p>Please click the link below to verify your email address:</p>
                <a href="{verification_link}">Verify Email</a>
                <p>This link will expire in 24 hours.</p>
            </body>
        </html>
        """
        
        send_email(
            to_email=email,
            subject=subject,
            html_content=body
        )
        
        logger.info(f"Verification email sent to {email}")
        
        return {"status": "success", "email": email}
        
    except Exception as e:
        logger.error(f"Error sending verification email to {email}: {str(e)}")
        raise

### Send order confirmation email
@celery_app.task(name="send_order_confirmation")
def send_order_confirmation_task(
    email: str,
    order_number: str,
    order_details: dict
):
    try:
        subject = f"Order Confirmation - {order_number}"
        body = f"""
        <html>
            <body>
                <h2>Order Confirmed</h2>
                <p>Thank you for your order!</p>
                <p><strong>Order Number:</strong> {order_number}</p>
                <p><strong>Total Amount:</strong> ₦{order_details['total_amount']:,.2f}</p>
                <p>We'll send you another email when your order ships.</p>
            </body>
        </html>
        """
        
        send_email(
            to_email=email,
            subject=subject,
            html_content=body
        )
        
        logger.info(f"Order confirmation sent to {email}")
        
        return {"status": "success", "email": email}
        
    except Exception as e:
        logger.error(f"Error sending order confirmation to {email}: {str(e)}")
        raise

### Notify when BOQ is approved
@celery_app.task(name="send_boq_approved_notification")
def send_boq_approved_notification_task(
    email: str,
    boq_number: str,
    project_name: str
):
    try:
        subject = f"BOQ Approved - {boq_number}"
        body = f"""
        <html>
            <body>
                <h2>BOQ Approved</h2>
                <p>Your Bill of Quantities has been approved:</p>
                <p><strong>BOQ Number:</strong> {boq_number}</p>
                <p><strong>Project:</strong> {project_name}</p>
                <p>You can now proceed with procurement.</p>
            </body>
        </html>
        """
        
        send_email(
            to_email=email,
            subject=subject,
            html_content=body
        )
        
        logger.info(f"BOQ approval notification sent to {email}")
        
        return {"status": "success", "email": email}
        
    except Exception as e:
        logger.error(f"Error sending BOQ approval to {email}: {str(e)}")
        raise
    