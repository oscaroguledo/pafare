"""Webhook Routes - Simple Stripe webhook handling without storage."""
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from core.db import get_db
from services.commerce.webhooks import WebhookService
from core.logging import get_structured_logger as get_logger
from core.utils.response import Response

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/stripe/")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Handle Stripe webhooks with signature verification; processes immediately, no storage."""
    try:
        # Get request body and signature
        payload = await request.body()
        sig_header = request.headers.get("stripe-signature")
        
        if not sig_header:
            raise HTTPException(status_code=400, detail="Missing Stripe signature")
        
        # Get client IP for logging (but don't pass to webhook service)
        client_ip = request.client.host if request.client else None
        logger.info(f"Webhook request from IP: {client_ip}")
        
        # Process webhook
        webhook_service = WebhookService(db)
        result = await webhook_service.handle_stripe_webhook(
            request=request,
            request_body=payload,
            signature=sig_header
        )
        
        return Response.success(data=result)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in webhook handler: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/health/")
async def webhook_health():
    """Health check endpoint for webhook service"""
    return Response.success(data={"status": "healthy", "service": "webhooks"})