"""Shipping tracking service; integrates with multiple carriers (UPS, Royal Mail, etc.)."""

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from models.commerce.orders import Order, PaymentStatus
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload

from models.commerce.shipping_tracking import (
    ShipmentTracking, ShipmentTrackingEvent as TrackingEvent, ShippingProvider,
    TrackingStatus, ShipmentType
)
from models.commerce.carriers import Carrier
from services.commerce.carrier_integrations import (
    UPSIntegration, CanadaExpressIntegration, RoyalMailIntegration, FedExIntegration,
    DHLIntegration, USPSIntegration, CanadaPostIntegration, PurolatorIntegration,
)
from core.exceptions import APIException
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)


class ShippingTrackingService:
    """Service for managing shipping tracking across multiple carriers"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.carrier_integrations = {
            "ups": UPSIntegration(),
            "canada_express": CanadaExpressIntegration(),
            "royal_mail": RoyalMailIntegration(),
            "fedex": FedExIntegration(),
            "dhl": DHLIntegration(),
            "usps": USPSIntegration(),
            "canada_post": CanadaPostIntegration(),
            "purolator": PurolatorIntegration(),
        }

    async def _get_active_carrier(self, code: str) -> Carrier:
        """Resolve a carrier code (e.g. "ups") to its active Carrier row."""
        result = await self.db.execute(
            select(Carrier).where(and_(Carrier.code == code, Carrier.is_active == True))
        )
        carrier = result.scalar_one_or_none()
        if not carrier:
            raise APIException(status_code=400, message=f"Carrier '{code}' not found or inactive")
        return carrier

    async def create(self, shipment_data: Dict[str, Any]) -> ShipmentTracking:
        """Create a new shipment tracking record"""
        try:
            order = await self.db.get(Order, shipment_data['order_id'])
            if not order:
                raise APIException(status_code=404, message="Order not found")
            # Nothing ships until the payment has gone through.
            if order.payment_status != PaymentStatus.PAID:
                raise APIException(status_code=400, message="This order hasn't been paid yet, so it can't be shipped")
            carrier = await self._get_active_carrier(shipment_data['carrier'])

            # The carrier's first active API account, if any; without one the shipment is tracked by hand.
            provider_result = await self.db.execute(
                select(ShippingProvider).where(
                    and_(
                        ShippingProvider.carrier_id == carrier.id,
                        ShippingProvider.is_active == True
                    )
                ).limit(1)
            )
            provider = provider_result.scalars().first()

            # Create shipment
            shipment = ShipmentTracking(
                order_id=shipment_data['order_id'],
                order_item_id=shipment_data.get('order_item_id'),
                provider_id=provider.id if provider else None,
                tracking_number=shipment_data['tracking_number'],
                carrier_id=carrier.id,
                shipment_type=shipment_data.get('shipment_type', ShipmentType.STANDARD),
                origin_address=shipment_data.get('origin_address'),
                destination_address=shipment_data.get('destination_address'),
                current_location=shipment_data.get('origin_address'),
                delivery_instructions=shipment_data.get('delivery_instructions'),
                package_weight=shipment_data.get('package_weight'),
                package_dimensions=shipment_data.get('package_dimensions'),
                package_value=shipment_data.get('package_value'),
                insurance_amount=shipment_data.get('insurance_amount'),
                service_level=shipment_data.get('service_level'),
                delivery_signature_required=shipment_data.get('delivery_signature_required', False),
                delivery_confirmation=shipment_data.get('delivery_confirmation'),
                notes=shipment_data.get('notes'),
                internal_notes=shipment_data.get('internal_notes')
            )

            # Populate relationships in-memory so to_dict() doesn't need a lazy load
            # (the object was constructed directly, not loaded via a selectinload query)
            shipment.carrier = carrier
            shipment.provider = provider
            shipment.tracking_events = []

            self.db.add(shipment)
            await self.db.flush()

            # Create initial tracking event
            if shipment_data.get('shipped_at'):
                initial_event = await self._create_tracking_event(
                    shipment.id,
                    "shipped",
                    "Package shipped",
                    shipment_data.get('origin_address'),
                    shipment_data['shipped_at']
                )
                # Keep the in-memory collection in sync (set to [] above to avoid a lazy-load) -
                # otherwise this event is invisible to to_dict() since it's already "loaded".
                shipment.tracking_events.append(initial_event)

            await self.db.commit()
            return shipment

        except APIException:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            raise APIException(status_code=500, message=f"Failed to create shipment: {str(e)}")

    async def track_shipment(self, tracking_number: str, carrier: str) -> Dict[str, Any]:
        """Track a shipment using carrier-specific integration"""
        try:
            carrier_row = await self._get_active_carrier(carrier)

            # Get shipment record
            shipment_result = await self.db.execute(
                select(ShipmentTracking).where(
                    and_(
                        ShipmentTracking.tracking_number == tracking_number,
                        ShipmentTracking.carrier_id == carrier_row.id
                    )
                ).options(
                    selectinload(ShipmentTracking.tracking_events),
                    selectinload(ShipmentTracking.carrier),
                    selectinload(ShipmentTracking.provider),
                )
            )
            shipment = shipment_result.scalar_one_or_none()

            if not shipment:
                raise APIException(status_code=404, message="Shipment not found")

            # Get carrier integration
            integration = self.carrier_integrations.get(carrier)
            if not integration:
                raise APIException(status_code=400, message=f"Carrier integration not available for {carrier}")

            # Get provider configuration
            provider_result = await self.db.execute(
                select(ShippingProvider).where(ShippingProvider.id == shipment.provider_id)
            )
            provider = provider_result.scalar_one_or_none()

            # Track shipment using carrier API
            tracking_data = await integration.track_shipment(
                tracking_number, 
                provider.configuration if provider else {}
            )

            # Update shipment with latest data
            await self._update_shipment_from_tracking_data(shipment, tracking_data)

            # Create tracking events for new updates
            await self._process_tracking_events(shipment, tracking_data.get('events', []))

            # Update sync status
            shipment.last_api_sync = datetime.now(timezone.utc)
            shipment.sync_status = "success"
            shipment.external_tracking_data = tracking_data

            await self.db.commit()
            # updated_at is DB-computed and stale after this UPDATE regardless of
            # expire_on_commit, so refresh it before to_dict() reads it below.
            await self.db.refresh(shipment, attribute_names=["updated_at"])

            return {
                "shipment": shipment.to_dict(),
                "tracking_data": tracking_data
            }

        except APIException:
            raise
        except Exception as e:
            # Update sync status on error
            if 'shipment' in dir() and shipment:
                shipment.sync_status = "error"
                shipment.last_api_sync = datetime.now(timezone.utc)
                await self.db.commit()
            raise APIException(status_code=500, message=f"Failed to track shipment: {str(e)}")

    async def get(self, shipment_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific shipment by ID"""
        try:
            shipment_result = await self.db.execute(
                select(ShipmentTracking).where(
                    ShipmentTracking.id == shipment_id
                ).options(
                    selectinload(ShipmentTracking.tracking_events),
                    selectinload(ShipmentTracking.carrier),
                    selectinload(ShipmentTracking.provider),
                )
            )
            shipment = shipment_result.scalar_one_or_none()
            return shipment.to_dict() if shipment else None
        except Exception as e:
            raise APIException(status_code=500, message=f"Failed to get shipment: {str(e)}")


    async def list_by_order(self, order_id: str) -> List[Dict[str, Any]]:
        """Get all shipments for an order"""
        try:
            shipments_result = await self.db.execute(
                select(ShipmentTracking).where(
                    ShipmentTracking.order_id == order_id
                ).options(
                    selectinload(ShipmentTracking.tracking_events),
                    selectinload(ShipmentTracking.carrier),
                    selectinload(ShipmentTracking.provider),
                ).order_by(ShipmentTracking.created_at.desc())
            )
            shipments = shipments_result.scalars().all()
            return [shipment.to_dict() for shipment in shipments]
        except Exception as e:
            raise APIException(status_code=500, message=f"Failed to list shipments: {str(e)}")

    async def update(self, shipment_id: str, status: TrackingStatus, 
                                   event_data: Dict[str, Any] = None) -> ShipmentTracking:
        """Update shipment status and create tracking event"""
        try:
            shipment_result = await self.db.execute(
                select(ShipmentTracking).where(ShipmentTracking.id == shipment_id)
            )
            shipment = shipment_result.scalar_one_or_none()

            if not shipment:
                raise APIException(status_code=404, message="Shipment not found")

            shipment.status = status

            # Update delivery timestamps
            if status == TrackingStatus.DELIVERED and not shipment.actual_delivery:
                shipment.actual_delivery = datetime.now(timezone.utc)

            # Create tracking event
            await self._create_tracking_event(
                shipment.id,
                status.value,
                event_data.get('description', f"Status updated to {status.value}"),
                event_data.get('location'),
                datetime.now(timezone.utc),
                event_data
            )

            await self.db.commit()

            # Re-fetch with eager-loading: relationships accessed by to_dict() are
            # not safely readable off the pre-commit object after a mutate+commit.
            result = await self.db.execute(
                select(ShipmentTracking).where(ShipmentTracking.id == shipment.id).options(
                    selectinload(ShipmentTracking.tracking_events),
                    selectinload(ShipmentTracking.carrier),
                    selectinload(ShipmentTracking.provider),
                )
            )
            return result.scalar_one()

        except APIException:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            raise APIException(status_code=500, message=f"Failed to update shipment status: {str(e)}")

    async def _update_shipment_from_tracking_data(self, shipment: ShipmentTracking, 
                                                tracking_data: Dict[str, Any]):
        """Update shipment with data from carrier API"""
        # Update status
        if 'status' in tracking_data:
            try:
                shipment.status = TrackingStatus(tracking_data['status'].lower())
            except ValueError:
                pass  # Keep existing status if invalid

        # Update location
        if 'current_location' in tracking_data:
            shipment.current_location = tracking_data['current_location']

        # Update estimated delivery
        if 'estimated_delivery' in tracking_data:
            try:
                shipment.estimated_delivery = datetime.fromisoformat(
                    tracking_data['estimated_delivery'].replace('Z', '+00:00')
                )
            except (ValueError, AttributeError):
                pass

        # Update delivery info
        if 'actual_delivery' in tracking_data:
            try:
                shipment.actual_delivery = datetime.fromisoformat(
                    tracking_data['actual_delivery'].replace('Z', '+00:00')
                )
            except (ValueError, AttributeError):
                pass

    async def _process_tracking_events(self, shipment: ShipmentTracking, 
                                       events: List[Dict[str, Any]]):
        """Process tracking events from carrier API"""
        existing_events = {event.event_timestamp.isoformat(): event for event in shipment.tracking_events}
        
        for event_data in events:
            try:
                event_timestamp = datetime.fromisoformat(
                    event_data['timestamp'].replace('Z', '+00:00')
                )
                
                # Skip if event already exists
                if event_timestamp.isoformat() in existing_events:
                    continue

                new_event = await self._create_tracking_event(
                    shipment.id,
                    event_data.get('event_type', 'unknown'),
                    event_data.get('description', ''),
                    event_data.get('location'),
                    event_timestamp,
                    event_data
                )
                # Keep the in-memory collection in sync so an immediate to_dict()
                # reflects events just created here, not just what was loaded earlier.
                shipment.tracking_events.append(new_event)

            except (ValueError, KeyError) as e:
                logger.error(f"Error processing tracking event: {e}")
                continue

    async def _create_tracking_event(self, shipment_id: str, event_type: str,
                                    description: str, location: Dict[str, Any] = None,
                                    timestamp: datetime = None, additional_data: Dict[str, Any] = None) -> TrackingEvent:
        """Create a tracking event"""
        event = TrackingEvent(
            shipment_id=shipment_id,
            event_timestamp=timestamp or datetime.now(timezone.utc),
            event_type=event_type,
            event_description=description,
            event_location=location,
            carrier_event_code=additional_data.get('carrier_event_code') if additional_data else None,
            carrier_event_data=additional_data.get('carrier_event_data') if additional_data else None,
            estimated_delivery=additional_data.get('estimated_delivery') if additional_data else None,
            delay_reason=additional_data.get('delay_reason') if additional_data else None,
            exception_details=additional_data.get('exception_details') if additional_data else None,
            contact_name=additional_data.get('contact_name') if additional_data else None,
            contact_phone=additional_data.get('contact_phone') if additional_data else None,
            source=additional_data.get('source', 'api') if additional_data else 'api',
            raw_data=additional_data if additional_data else {}
        )

        self.db.add(event)
        return event
