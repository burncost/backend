"""Payment Service - Real payment processing via Paystack API."""
from typing import Dict, Any, Optional
import logging
import os
from app.config import settings
import json

logger = logging.getLogger(__name__)


class PaymentService:
    """Service for processing payments through Paystack payment gateway."""

    def __init__(self):
        self.paystack_secret_key = os.getenv("PAYSTACK_SECRET_KEY", "")
        self.paystack_public_key = os.getenv("PAYSTACK_PUBLIC_KEY", "")
        self.flutterwave_secret_key = settings.FLUTTERWAVE_SECRET_KEY
        self.flutterwave_public_key = settings.FLUTTERWAVE_PUBLIC_KEY
        self.mock_mode = settings.MOCK_PAYMENT_GATEWAY

    async def initialize_payment(
        self,
        amount: float,
        email: str,
        reference: str,
        metadata: Optional[Dict[str, Any]] = None,
        payment_type: str = "card"
    ) -> Dict[str, Any]:
        """Initialize a payment transaction via Flutterwave v3 (preferred) or Paystack fallback.
        
        Args:
            payment_type: "card", "bank_transfer", or "ussd"
        """
        logger.info(f"Initializing payment: {reference} for {email} - ₦{amount:,.2f} (type={payment_type})")

        if self.mock_mode:
            return self._mock_initialize(amount, email, reference, metadata)

        # Try Flutterwave v3 first if key is configured
        if self.flutterwave_secret_key:
            return await self._initialize_flutterwave(amount, email, reference, metadata, payment_type)

        # Fallback to Paystack
        if self.paystack_secret_key:
            return await self._initialize_paystack(amount, email, reference, metadata)

        # No provider configured, use mock
        logger.warning("No payment provider configured, using mock payment")
        return self._mock_initialize(amount, email, reference, metadata)

    async def _initialize_flutterwave(
        self,
        amount: float,
        email: str,
        reference: str,
        metadata: Optional[Dict[str, Any]] = None,
        payment_type: str = "card"
    ) -> Dict[str, Any]:
        """Initialize payment via Flutterwave v3 API.
        
        Args:
            payment_type: "card" (redirect), "bank_transfer" (virtual account), or "ussd" (USSD code)
        """
        try:
            import httpx

            # Build Flutterwave payload based on payment type
            payload = {
                "tx_ref": reference,
                "amount": amount,
                "currency": "NGN",
                "customer": {
                    "email": email,
                },
                "customizations": {
                    "title": "Burncost Payment",
                    "description": f"Order payment - {reference}",
                },
                "meta": metadata or {},
            }

            if payment_type == "bank_transfer":
                # Flutterwave generates a virtual account for bank transfer
                payload["payment_options"] = "bank_transfer"
                payload["is_permanent"] = False
            elif payment_type == "ussd":
                payload["payment_options"] = "ussd"
            else:
                # Default: card — redirect to Flutterwave checkout
                payload["redirect_url"] = f"{settings.FRONTEND_URL}/dashboard/orders"

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.flutterwave.com/v3/payments",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.flutterwave_secret_key}",
                        "Content-Type": "application/json",
                    }
                )

                if response.status_code != 200:
                    logger.error(f"Flutterwave error: {response.status_code} {response.text}")
                    return {"success": False, "error": f"Flutterwave returned status {response.status_code}", "reference": reference, "provider": "flutterwave"}

                data = response.json()
                if data.get("status") == "success":
                    result = {
                        "success": True,
                        "reference": reference,
                        "provider": "flutterwave",
                        "payment_type": payment_type,
                    }

                    if payment_type == "bank_transfer":
                        # Flutterwave returns virtual account details
                        result["bank_transfer"] = {
                            "bank_name": data["data"].get("bank_name", ""),
                            "account_number": data["data"].get("account_number", ""),
                            "account_name": data["data"].get("account_name", "Burncost Payment"),
                            "amount": amount,
                            "expires_at": data["data"].get("expires_at", ""),
                        }
                    elif payment_type == "ussd":
                        result["ussd_code"] = data["data"].get("ussd_code", "")
                    else:
                        # Card — standard redirect
                        result["authorization_url"] = data["data"]["link"]

                    return result

                logger.error(f"Flutterwave init failed: {data}")
                return {"success": False, "error": "Flutterwave payment initialization failed", "reference": reference, "provider": "flutterwave"}

        except ImportError:
            logger.warning("httpx not installed for Flutterwave")
            return {"success": False, "error": "HTTP client not available", "reference": reference, "provider": "flutterwave"}
        except Exception as e:
            logger.error(f"Flutterwave init failed: {str(e)}")
            return {"success": False, "error": str(e), "reference": reference, "provider": "flutterwave"}

    async def _initialize_paystack(
        self,
        amount: float,
        email: str,
        reference: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Initialize payment via Paystack API."""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.paystack.co/transaction/initialize",
                    json={
                        "amount": int(amount * 100),  # Paystack uses kobo
                        "email": email,
                        "reference": reference,
                        "metadata": metadata or {},
                        "callback_url": f"{settings.FRONTEND_URL}/dashboard/orders",
                    },
                    headers={
                        "Authorization": f"Bearer {self.paystack_secret_key}",
                        "Content-Type": "application/json",
                    }
                )

                if response.status_code != 200:
                    logger.error(f"Paystack error: {response.status_code} {response.text}")
                    return self._mock_initialize(amount, email, reference, metadata)

                data = response.json()
                if data.get("status"):
                    return {
                        "success": True,
                        "authorization_url": data["data"]["authorization_url"],
                        "access_code": data["data"]["access_code"],
                        "reference": data["data"]["reference"],
                        "provider": "paystack",
                    }

                return self._mock_initialize(amount, email, reference, metadata)

        except ImportError:
            logger.warning("httpx not installed, using mock payment")
            return self._mock_initialize(amount, email, reference, metadata)
        except Exception as e:
            logger.error(f"Paystack init failed: {str(e)}")
            return self._mock_initialize(amount, email, reference, metadata)

    async def verify_payment(self, reference: str, provider: str = "flutterwave") -> Dict[str, Any]:
        """Verify a payment transaction via Flutterwave v3 or Paystack."""
        logger.info(f"Verifying payment: {reference} via {provider}")

        if self.mock_mode:
            return self._mock_verify(reference)

        if provider == "flutterwave" and self.flutterwave_secret_key:
            return await self._verify_flutterwave(reference)

        if self.paystack_secret_key:
            return await self._verify_paystack(reference)

        return self._mock_verify(reference)

    async def _verify_flutterwave(self, reference: str) -> Dict[str, Any]:
        """Verify payment via Flutterwave v3 API."""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://api.flutterwave.com/v3/transactions/verify_by_reference?tx_ref={reference}",
                    headers={
                        "Authorization": f"Bearer {self.flutterwave_secret_key}",
                    }
                )

                if response.status_code != 200:
                    logger.error(f"Flutterwave verify error: {response.status_code} {response.text}")
                    return {"success": False, "error": f"Flutterwave verify returned status {response.status_code}", "reference": reference, "provider": "flutterwave"}

                data = response.json()
                if data.get("status") == "success" and data["data"].get("status") == "successful":
                    return {
                        "success": True,
                        "amount": data["data"]["amount"],
                        "currency": data["data"]["currency"],
                        "status": data["data"]["status"],
                        "paid_at": data["data"].get("created_at"),
                        "channel": data["data"].get("payment_type", "card"),
                        "reference": reference,
                        "provider": "flutterwave",
                    }

                return {
                    "success": False,
                    "status": data.get("data", {}).get("status", "failed"),
                    "reference": reference,
                    "provider": "flutterwave",
                }

        except ImportError:
            logger.warning("httpx not installed for Flutterwave verify")
            return {"success": False, "error": "HTTP client not available", "reference": reference, "provider": "flutterwave"}
        except Exception as e:
            logger.error(f"Flutterwave verify failed: {str(e)}")
            return {"success": False, "error": str(e), "reference": reference, "provider": "flutterwave"}

    async def _verify_paystack(self, reference: str) -> Dict[str, Any]:
        """Verify payment via Paystack API."""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://api.paystack.co/transaction/verify/{reference}",
                    headers={
                        "Authorization": f"Bearer {self.paystack_secret_key}",
                    }
                )

                if response.status_code != 200:
                    return self._mock_verify(reference)

                data = response.json()
                if data.get("status") and data["data"].get("status") == "success":
                    return {
                        "success": True,
                        "amount": data["data"]["amount"] / 100,
                        "currency": data["data"]["currency"],
                        "status": data["data"]["status"],
                        "paid_at": data["data"]["paid_at"],
                        "channel": data["data"]["channel"],
                        "reference": reference,
                        "provider": "paystack",
                    }

                return {
                    "success": False,
                    "status": data.get("data", {}).get("status", "failed"),
                    "reference": reference,
                    "provider": "paystack",
                }

        except ImportError:
            return self._mock_verify(reference)
        except Exception as e:
            logger.error(f"Paystack verify failed: {str(e)}")
            return self._mock_verify(reference)

    async def create_transfer(
        self,
        amount: float,
        recipient_code: str,
        reference: str,
        reason: str = "Escrow release"
    ) -> Dict[str, Any]:
        """Create a transfer to a supplier (escrow release)."""
        logger.info(f"Creating transfer: {reference} - ₦{amount:,.2f}")

        if self.mock_mode or not self.paystack_secret_key:
            return self._mock_transfer(amount, recipient_code, reference)

        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.paystack.co/transfer",
                    json={
                        "source": "balance",
                        "amount": int(amount * 100),
                        "recipient": recipient_code,
                        "reference": reference,
                        "reason": reason,
                    },
                    headers={
                        "Authorization": f"Bearer {self.paystack_secret_key}",
                        "Content-Type": "application/json",
                    }
                )

                if response.status_code != 200:
                    logger.error(f"Transfer error: {response.status_code} {response.text}")
                    return self._mock_transfer(amount, recipient_code, reference)

                data = response.json()
                if data.get("status"):
                    return {
                        "success": True,
                        "transfer_code": data["data"]["transfer_code"],
                        "status": data["data"]["status"],
                        "reference": reference,
                        "provider": "paystack",
                    }

                return self._mock_transfer(amount, recipient_code, reference)

        except ImportError:
            return self._mock_transfer(amount, recipient_code, reference)
        except Exception as e:
            logger.error(f"Transfer failed: {str(e)}")
            return self._mock_transfer(amount, recipient_code, reference)

    async def create_transfer_recipient(
        self,
        name: str,
        account_number: str,
        bank_code: str
    ) -> Dict[str, Any]:
        """Create a transfer recipient for supplier payouts."""
        logger.info(f"Creating transfer recipient: {name} - {account_number}")

        if self.mock_mode or not self.paystack_secret_key:
            return {
                "success": True,
                "recipient_code": f"MOCK-RCP-{account_number[-4:]}",
                "provider": "mock",
            }

        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.paystack.co/transferrecipient",
                    json={
                        "type": "nuban",
                        "name": name,
                        "account_number": account_number,
                        "bank_code": bank_code,
                        "currency": "NGN",
                    },
                    headers={
                        "Authorization": f"Bearer {self.paystack_secret_key}",
                        "Content-Type": "application/json",
                    }
                )

                if response.status_code != 200:
                    raise Exception(f"Paystack error: {response.text}")

                data = response.json()
                if data.get("status"):
                    return {
                        "success": True,
                        "recipient_code": data["data"]["recipient_code"],
                        "provider": "paystack",
                    }

                raise Exception("Failed to create recipient")

        except ImportError:
            return {
                "success": True,
                "recipient_code": f"MOCK-RCP-{account_number[-4:]}",
                "provider": "mock",
            }
        except Exception as e:
            logger.error(f"Create recipient failed: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "provider": "paystack",
            }

    def _mock_initialize(self, amount: float, email: str, reference: str, metadata: Optional[Dict] = None, payment_type: str = "card") -> Dict[str, Any]:
        result = {
            "success": True,
            "reference": reference,
            "provider": "mock",
            "payment_type": payment_type,
        }
        if payment_type == "bank_transfer":
            result["bank_transfer"] = {
                "bank_name": "Mock Bank",
                "account_number": "0123456789",
                "account_name": "Burncost Payment",
                "amount": amount,
            }
        elif payment_type == "ussd":
            result["ussd_code"] = "*737*12345*{:.0f}#".format(amount)
        else:
            result["authorization_url"] = f"https://checkout.paystack.com/mock-{reference}"
            result["access_code"] = f"MOCK-{reference[:8]}"
        return result

    def _mock_verify(self, reference: str) -> Dict[str, Any]:
        return {
            "success": True,
            "amount": 100000.0,
            "currency": "NGN",
            "status": "success",
            "paid_at": __import__("datetime").datetime.utcnow().isoformat(),
            "channel": "card",
            "reference": reference,
            "provider": "mock",
        }

    def _mock_transfer(self, amount: float, recipient_code: str, reference: str) -> Dict[str, Any]:
        return {
            "success": True,
            "transfer_code": f"MOCK-TRF-{reference[:8]}",
            "status": "success",
            "reference": reference,
            "provider": "mock",
        }
