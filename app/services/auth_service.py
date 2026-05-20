from requests import options
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import logging

from email.mime.text import MIMEText
from fastapi import BackgroundTasks

import smtplib

from app.core.security import verify_email_token, create_email_verification_token, decode_token
from app.crud import user as user_crud
from app.config import settings

from app.utils.helpers import send_mail_via_brevo, send_mail_via_spacemail, verify_business

logger = logging.getLogger(__name__)

API_URL = settings.API_URL

class AuthService:

    def send_email(self, recipient: str, verification_link: str):
        # logger.info(f"\n\n\nSending verification email to {recipient} with link: {verification_link}\n\n")
        # send_sms_via_spacemail(verification_link, recipient)       
        pass
        
    async def send_verification_email(self, email: str):
        token = create_email_verification_token(email)
        verification_link = f"{settings.API_URL}/auth/verify-email?token={token}"

        # verification_details = await verify_business("9436936")
        # logger.info(f"\n\nVerification details: {verification_details}\n\n")

        # CALL the Gmail sender
        logger.info(f"\n\n\nSending verification email to {email} with link: {verification_link}\n\n")
        # self.send_email(email, verification_link)

        success = await send_mail_via_spacemail(verification_link, email, "Verify your email")
        if success:
            logger.info(f"Mail delivered successfully to {email} via Spacemail")
        else:
            await send_mail_via_brevo(verification_link, email, "Verify your email")
            logger.info(f"Mail delivered successfully to {email} via Brevo API")

    async def verify_email(self, db: AsyncSession, token: str) -> bool:
        email = verify_email_token(token)
        if not email:
            return False

        user = await user_crud.get_by_email(db, email=email)
        if not user:
            return False
        role = user.role.value
        await user_crud.verify_email(db, user_id=user.id)
        # return True
        return role
    
    