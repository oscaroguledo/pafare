"""Business analytics: conversion rates, cart abandonment, refund rates, repeat customers."""
from datetime import datetime, timezone, timedelta, date
from typing import Dict, Any, Optional
from uuid import UUID
import secrets
from core.utils.uuid_utils import uuid7
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, exists
from sqlalchemy.orm import aliased
from sqlalchemy.dialects.postgresql import insert as pg_insert
from fastapi import HTTPException

from models.accounts import UserSession, TrafficSource
from models.system import AnalyticsEvent, ConversionFunnel, EventType
from models.commerce.orders import Order, OrderStatus
from models.accounts.user import User, Role, STAFF_ROLES
from models.commerce.refunds import Refund, RefundStatus
from models.commerce.subscriptions import Subscription
from models.catalog.product import Product, ProductStatus
from core.logging import get_structured_logger
from typing import Dict, Any, Optional
from models.commerce.orders import Order, OrderStatus
from models.catalog.product import Product, ProductVariant, ProductStatus
from models.catalog.category import Category
from services.catalog.category import category_ids_matching
from models.catalog.inventories import Inventory

STAFF_ROLE_VALUES = [r.value for r in STAFF_ROLES]  # staff aren't counted as customers

logger = get_structured_logger(__name__)


class AnalyticsService:
    """Comprehensive business analytics service"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def track_event(
        self,
        session_id: str,
        event_type: EventType,
        user_id: Optional[UUID] = None,
        event_data: Optional[Dict[str, Any]] = None,
        page_url: Optional[str] = None,
        page_title: Optional[str] = None,
        order_id: Optional[UUID] = None,
        product_id: Optional[UUID] = None,
        revenue: Optional[float] = None,
        session_info: Optional[Dict[str, Any]] = None
    ) -> AnalyticsEvent:
        """Track an analytics event"""
        try:
            # Auto-generate session_id if not provided
            if not session_id:
                session_id = secrets.token_hex(16)

            # Create the session if absent; ON CONFLICT makes concurrent first events of a visit safe.
            info = session_info or {}
            source = info.get("traffic_source")
            await self.db.execute(
                pg_insert(UserSession).values(
                    id=uuid7(),
                    session_id=session_id,
                    user_id=user_id,
                    started_at=datetime.now(timezone.utc),
                    # Attribution is recorded once, from the first event of the session.
                    traffic_source=TrafficSource(source) if source in {t.value for t in TrafficSource} else TrafficSource.DIRECT,
                    referrer_url=info.get("referrer_url") or None,
                    utm_source=info.get("utm_source"),
                    utm_medium=info.get("utm_medium"),
                    utm_campaign=info.get("utm_campaign"),
                ).on_conflict_do_nothing(index_elements=["session_id"])
            )

            event = AnalyticsEvent(
                id=uuid7(),
                session_id=session_id,
                user_id=user_id,
                event_type=event_type,
                page_url=page_url,
                page_title=page_title,
                event_data=event_data or {},
                order_id=order_id,
                product_id=product_id,
                revenue=revenue
            )
            
            self.db.add(event)
            # _update_session_metrics() below queries for this same event, so flush
            # explicitly rather than relying on autoflush.
            await self.db.flush()

            # Update session metrics
            await self._update_session_metrics(session_id)
            
            # Update conversion funnel
            if user_id:
                await self._update_conversion_funnel(session_id, user_id, event_type)
            
            await self.db.commit()
            return event
            
        except Exception as e:
            logger.error(f"Failed to track event: {e}")
            await self.db.rollback()
            raise
    
    async def get_conversion_metrics(
        self,
        start_date: datetime,
        end_date: datetime,
        traffic_source: Optional[TrafficSource] = None
    ) -> Dict[str, Any]:
        """Conversion from tracked sessions (a session converts when it records a purchase event)."""
        period = {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()}
        in_period = and_(UserSession.started_at >= start_date, UserSession.started_at <= end_date)
        if traffic_source:
            in_period = and_(in_period, UserSession.traffic_source == traffic_source)
        # Checked per session in the period, so the cost doesn't grow with all-time purchase history.
        purchased = exists().where(
            AnalyticsEvent.session_id == UserSession.session_id,
            AnalyticsEvent.event_type == EventType.PURCHASE,
        )
        rows = (await self.db.execute(
            select(
                UserSession.traffic_source,
                func.count(UserSession.id),
                func.count(UserSession.id).filter(purchased),
            ).where(in_period).group_by(UserSession.traffic_source)
        )).all()

        revenue_rows = (await self.db.execute(
            select(UserSession.traffic_source, func.coalesce(func.sum(AnalyticsEvent.revenue), 0))
            .join(AnalyticsEvent, AnalyticsEvent.session_id == UserSession.session_id)
            .where(in_period, AnalyticsEvent.event_type == EventType.PURCHASE)
            .group_by(UserSession.traffic_source)
        )).all()
        revenue_by_source = {src: float(total) for src, total in revenue_rows}

        orders_total, orders_avg = (await self.db.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0), func.coalesce(func.avg(Order.total_amount), 0))
            .where(Order.created_at >= start_date, Order.created_at <= end_date, Order.order_status.in_([OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED]))
        )).one()

        total_sessions = sum(r[1] for r in rows)
        converted = sum(r[2] for r in rows)
        rate = lambda c, t: round(c / t * 100, 2) if t else 0.0
        return {
            "period": period,
            "overall": {
                "total_sessions": total_sessions,
                "converted_sessions": converted,
                "conversion_rate": rate(converted, total_sessions),
                "total_revenue": float(orders_total),
                "average_order_value": float(orders_avg),
            },
            "by_traffic_source": [
                {
                    "traffic_source": src.value if hasattr(src, "value") else src,
                    "total_sessions": sessions,
                    "converted_sessions": conv,
                    "conversion_rate": rate(conv, sessions),
                    "revenue": revenue_by_source.get(src, 0.0),
                }
                for src, sessions, conv in sorted(rows, key=lambda r: -r[1])
            ],
        }

    async def get_cart_abandonment_metrics(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """Funnel from tracked events: distinct sessions reaching each step in the period."""
        in_period = and_(AnalyticsEvent.created_at >= start_date, AnalyticsEvent.created_at <= end_date)

        async def sessions_with(*conditions) -> int:
            return (await self.db.execute(
                select(func.count(func.distinct(AnalyticsEvent.session_id))).where(in_period, *conditions)
            )).scalar() or 0

        def reached(event_type):
            return AnalyticsEvent.session_id.in_(
                select(AnalyticsEvent.session_id).where(in_period, AnalyticsEvent.event_type == event_type)
            )

        landing = await sessions_with(AnalyticsEvent.event_type == EventType.PAGE_VIEW)
        product_view = await sessions_with(AnalyticsEvent.event_type == EventType.PAGE_VIEW, AnalyticsEvent.product_id.isnot(None))
        cart = await sessions_with(AnalyticsEvent.event_type == EventType.CART_ADD)
        # Later steps count only sessions that also did the step before, so a drop can't go negative.
        checkout = await sessions_with(AnalyticsEvent.event_type == EventType.CHECKOUT_START, reached(EventType.CART_ADD))
        purchase = await sessions_with(AnalyticsEvent.event_type == EventType.PURCHASE, reached(EventType.CHECKOUT_START), reached(EventType.CART_ADD))

        drop = lambda start, end: round((start - end) / start * 100, 2) if start else 0.0
        return {
            "period": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
            "abandonment_rates": {
                "cart_abandonment_rate": drop(cart, checkout),
                "checkout_abandonment_rate": drop(checkout, purchase),
                "overall_abandonment_rate": drop(cart, purchase),
            },
            "funnel_metrics": {
                "total_cart_sessions": cart,
                "total_checkout_sessions": checkout,
                "total_purchase_sessions": purchase,
            },
            "conversion_funnel": [
                {"step": 0, "step_name": "Landing", "count": landing},
                {"step": 1, "step_name": "Product View", "count": product_view},
                {"step": 2, "step_name": "Add to Cart", "count": cart},
                {"step": 3, "step_name": "Checkout Start", "count": checkout},
                {"step": 4, "step_name": "Purchase", "count": purchase},
            ],
        }

    async def get_time_to_purchase_metrics(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """Hours from sign-up to first paid order, for customers whose first order falls in the period."""
        paid = [OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED]
        # Start from the period's paid orders and drop customers who paid before it, rather than
        # finding every customer's first order ever.
        earlier = aliased(Order)
        paid_before = exists().where(
            earlier.user_id == Order.user_id,
            earlier.order_status.in_(paid),
            earlier.created_at < start_date,
        )
        rows = (await self.db.execute(
            select(User.created_at, func.min(Order.created_at))
            .join(User, User.id == Order.user_id)
            .where(Order.order_status.in_(paid), Order.created_at >= start_date, Order.created_at <= end_date, ~paid_before)
            .group_by(User.id, User.created_at)
        )).all()
        hours = sorted(max(0.0, (first - signed_up).total_seconds() / 3600) for signed_up, first in rows)

        buckets = [("0-1 hours", 0, 1), ("1-24 hours", 1, 24), ("1-7 days", 24, 168), ("1-4 weeks", 168, 672), ("1+ months", 672, float("inf"))]
        count = len(hours)
        median = (hours[count // 2] if count % 2 else (hours[count // 2 - 1] + hours[count // 2]) / 2) if count else 0
        average = sum(hours) / count if count else 0
        return {
            "period": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
            "metrics": {
                "total_first_purchases": count,
                "average_hours": round(average, 2),
                "median_hours": round(median, 2),
                "min_hours": round(hours[0], 2) if count else 0,
                "max_hours": round(hours[-1], 2) if count else 0,
                "average_days": round(average / 24, 2),
            },
            "distribution": [
                {"range": label, "count": sum(1 for h in hours if low <= h < high)} for label, low, high in buckets
            ] if count else [],
        }

    async def get_refund_rate_metrics(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """Get refund rate metrics computed from real Refund records."""
        try:
            # Orders in the period
            orders_result = await self.db.execute(
                select(
                    func.count(Order.id).label('total_orders'),
                    func.sum(Order.total_amount).label('total_revenue')
                ).where(
                    and_(
                        Order.created_at >= start_date,
                        Order.created_at <= end_date,
                        Order.order_status.in_(['CONFIRMED', 'SHIPPED', 'DELIVERED', 'PROCESSING'])
                    )
                )
            )
            order_stats = orders_result.first()
            total_orders = order_stats.total_orders or 0
            total_revenue = float(order_stats.total_revenue or 0)

            # Completed refunds in the period
            refund_amount_expr = func.coalesce(Refund.processed_amount, Refund.approved_amount, Refund.requested_amount)
            refunds_result = await self.db.execute(
                select(
                    func.count(Refund.id).label('total_refunds'),
                    func.sum(refund_amount_expr).label('total_refund_amount')
                ).where(
                    and_(
                        Refund.created_at >= start_date,
                        Refund.created_at <= end_date,
                        Refund.status == RefundStatus.COMPLETED
                    )
                )
            )
            refund_stats = refunds_result.first()
            total_refunds = refund_stats.total_refunds or 0
            total_refund_amount = float(refund_stats.total_refund_amount or 0)

            refund_rate = (total_refunds / total_orders * 100) if total_orders > 0 else 0.0
            refund_amount_rate = (total_refund_amount / total_revenue * 100) if total_revenue > 0 else 0.0

            # Breakdown by reason
            reason_result = await self.db.execute(
                select(
                    Refund.reason,
                    func.count(Refund.id).label('count'),
                    func.sum(refund_amount_expr).label('amount')
                ).where(
                    and_(
                        Refund.created_at >= start_date,
                        Refund.created_at <= end_date,
                        Refund.status == RefundStatus.COMPLETED
                    )
                ).group_by(Refund.reason)
            )
            by_reason = []
            for row in reason_result.all():
                reason_amount = float(row.amount or 0)
                by_reason.append({
                    "reason": row.reason.value if hasattr(row.reason, "value") else row.reason,
                    "count": row.count,
                    "amount": reason_amount,
                    "percentage": round((reason_amount / total_refund_amount * 100), 2) if total_refund_amount > 0 else 0.0
                })

            return {
                "period": {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat()
                },
                "overall": {
                    "total_orders": total_orders,
                    "total_refunds": total_refunds,
                    "refund_rate": round(refund_rate, 2),
                    "total_revenue": total_revenue,
                    "total_refund_amount": total_refund_amount,
                    "refund_amount_rate": round(refund_amount_rate, 2)
                },
                "by_reason": by_reason
            }

        except Exception as e:
            logger.error(f"Failed to get refund rate metrics: {e}")
            raise HTTPException(status_code=500, detail="Failed to retrieve refund rate metrics")
    
    async def get_repeat_customer_metrics(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """Repeat purchasing from paid orders in the period; segments are new (1 order), returning (2-3), loyal (4+)."""
        rows = (await self.db.execute(
            select(
                Order.user_id,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_amount), 0),
                func.min(Order.created_at),
                func.max(Order.created_at),
            )
            .where(Order.created_at >= start_date, Order.created_at <= end_date, Order.order_status.in_([OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED]))
            .group_by(Order.user_id)
        )).all()

        total = len(rows)
        repeat = [r for r in rows if r[1] > 1]
        gaps = [(last - first).total_seconds() / 86400 / (n - 1) for _, n, _, first, last in repeat]
        segments = {"new": [], "returning": [], "loyal": []}
        for row in rows:
            segments["new" if row[1] == 1 else "returning" if row[1] <= 3 else "loyal"].append(row)

        def segment(name: str) -> Dict[str, Any]:
            members = segments[name]
            n = len(members)
            return {
                "segment": name,
                "count": n,
                "average_orders": round(sum(m[1] for m in members) / n, 2) if n else 0,
                "average_ltv": round(float(sum(m[2] for m in members)) / n, 2) if n else 0,
            }

        frequency: Dict[int, int] = {}
        for row in rows:
            key = min(row[1], 4)
            frequency[key] = frequency.get(key, 0) + 1
        return {
            "period": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
            "overall": {
                "total_customers": total,
                "repeat_customers": len(repeat),
                "repeat_rate": round(len(repeat) / total * 100, 2) if total else 0,
                "average_days_between_orders": round(sum(gaps) / len(gaps), 1) if gaps else 0,
            },
            "by_segment": [segment("new"), segment("returning"), segment("loyal")],
            # order_count 4 means "4 or more"
            "frequency_distribution": [{"order_count": k, "customer_count": frequency.get(k, 0)} for k in (1, 2, 3, 4)],
        }

    async def get_comprehensive_dashboard_data(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """Get comprehensive dashboard data with all key metrics"""
        try:
            # Get all metrics in parallel
            conversion_metrics = await self.get_conversion_metrics(start_date, end_date)
            cart_metrics = await self.get_cart_abandonment_metrics(start_date, end_date)
            purchase_metrics = await self.get_time_to_purchase_metrics(start_date, end_date)
            refund_metrics = await self.get_refund_rate_metrics(start_date, end_date)
            repeat_metrics = await self.get_repeat_customer_metrics(start_date, end_date)
            
            return {
                "period": {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat()
                },
                "conversion": conversion_metrics,
                "cart_abandonment": cart_metrics,
                "time_to_purchase": purchase_metrics,
                "refunds": refund_metrics,
                "repeat_customers": repeat_metrics,
                "generated_at": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to get comprehensive dashboard data: {e}")
            raise HTTPException(status_code=500, detail="Failed to retrieve dashboard data")
    
    async def _update_session_metrics(self, session_id: str):
        """Update session metrics when events are tracked"""
        try:
            # Get session
            session_result = await self.db.execute(
                select(UserSession).where(UserSession.session_id == session_id)
            )
            session = session_result.scalar_one_or_none()
            
            if session:
                # Count events
                events_count = await self.db.execute(
                    select(func.count(AnalyticsEvent.id)).where(
                        AnalyticsEvent.session_id == session_id
                    )
                )
                session.events_count = events_count.scalar() or 0
                
                # Check for conversion
                purchase_event = await self.db.execute(
                    select(AnalyticsEvent).where(
                        and_(
                            AnalyticsEvent.session_id == session_id,
                            AnalyticsEvent.event_type == EventType.PURCHASE
                        )
                    ).limit(1)
                )
                
                if purchase_event.scalar_one_or_none():
                    session.converted = True
                    # Get conversion value
                    revenue_sum = await self.db.execute(
                        select(func.sum(AnalyticsEvent.revenue)).where(
                            and_(
                                AnalyticsEvent.session_id == session_id,
                                AnalyticsEvent.event_type == EventType.PURCHASE
                            )
                        )
                    )
                    session.conversion_value = revenue_sum.scalar() or 0
                
        except Exception as e:
            logger.error(f"Failed to update session metrics: {e}")
    
    async def _update_conversion_funnel(self, session_id: str, user_id: UUID, event_type: EventType):
        """Update conversion funnel tracking"""
        try:
            # Get or create funnel record
            funnel_result = await self.db.execute(
                select(ConversionFunnel).where(
                    ConversionFunnel.session_id == session_id
                )
            )
            funnel = funnel_result.scalar_one_or_none()
            
            if not funnel:
                funnel = ConversionFunnel(
                    id=uuid7(),
                    session_id=session_id,
                    user_id=user_id
                )
                self.db.add(funnel)
            
            # Update funnel step based on event type
            now = datetime.now(timezone.utc)
            
            if event_type == EventType.PAGE_VIEW and not funnel.landing_at:
                funnel.landing_at = now
                funnel.current_step = max(funnel.current_step or 0, 0)
                funnel.max_step_reached = max(funnel.max_step_reached or 0, 0)
            elif event_type == EventType.CART_ADD:
                funnel.cart_add_at = now
                funnel.current_step = max(funnel.current_step or 0, 2)
                funnel.max_step_reached = max(funnel.max_step_reached or 0, 2)
            elif event_type == EventType.CHECKOUT_START:
                funnel.checkout_start_at = now
                funnel.current_step = max(funnel.current_step or 0, 3)
                funnel.max_step_reached = max(funnel.max_step_reached or 0, 3)
            elif event_type == EventType.PURCHASE:
                funnel.purchase_at = now
                funnel.current_step = 4
                funnel.max_step_reached = 4
                funnel.completed = True
                
        except Exception as e:
            logger.error(f"Failed to update conversion funnel: {e}")

    async def get_users_growth_trend(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """Get users growth trend data over the specified period"""
        try:
            # Get daily user registrations
            daily_users = await self.db.execute(
                select(
                    func.date(User.created_at).label('date'),
                    func.count(User.id).label('user_count')
                ).where(
                    and_(
                        User.created_at >= start_date,
                        User.created_at <= end_date,
                        User.role_id.notin_(select(Role.id).where(Role.name.in_(STAFF_ROLE_VALUES)))
                    )
                ).group_by(func.date(User.created_at)).order_by(func.date(User.created_at))
            )

            trend_data = []
            total_users = 0

            for row in daily_users:
                daily_count = row.user_count or 0
                total_users += daily_count

                trend_data.append({
                    "date": row.date.isoformat() if row.date else None,
                    "users": daily_count,
                    "cumulative_users": total_users
                })

            return {
                "period": {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat()
                },
                "trend_data": trend_data,
                "total_new_users": total_users
            }

        except Exception as e:
            logger.error(f"Failed to get users growth trend: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to retrieve users growth trend: {str(e)}")

    async def get_sales_trend_data(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """Get sales trend data over the specified period"""
        try:
            # Get daily sales data
            daily_sales = await self.db.execute(
                select(
                    func.date(Order.created_at).label('date'),
                    func.count(Order.id).label('order_count'),
                    func.sum(Order.total_amount).label('revenue'),
                    func.avg(Order.total_amount).label('avg_order_value')
                ).where(
                    and_(
                        Order.created_at >= start_date,
                        Order.created_at <= end_date,
                        Order.order_status.in_(['CONFIRMED', 'SHIPPED', 'DELIVERED', 'PROCESSING'])
                    )
                ).group_by(func.date(Order.created_at)).order_by(func.date(Order.created_at))
            )
            
            trend_data = []
            total_revenue = 0
            total_orders = 0
            
            for row in daily_sales:
                daily_revenue = float(row.revenue or 0)
                daily_orders = row.order_count or 0
                
                trend_data.append({
                    "date": row.date.isoformat() if row.date else None,
                    "order_count": daily_orders,
                    "revenue": daily_revenue,
                    "avg_order_value": float(row.avg_order_value or 0)
                })
                
                total_revenue += daily_revenue
                total_orders += daily_orders
            
            # Calculate growth rate if we have data
            growth_rate = 0.0
            if len(trend_data) > 1:
                first_day_revenue = trend_data[0]["revenue"]
                last_day_revenue = trend_data[-1]["revenue"]
                if first_day_revenue > 0:
                    growth_rate = ((last_day_revenue - first_day_revenue) / first_day_revenue) * 100
            
            return {
                "period": {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "days": len(trend_data)
                },
                "summary": {
                    "total_revenue": total_revenue,
                    "total_orders": total_orders,
                    "avg_daily_revenue": total_revenue / len(trend_data) if trend_data else 0,
                    "avg_daily_orders": total_orders / len(trend_data) if trend_data else 0,
                    "growth_rate": round(growth_rate, 2)
                },
                "sales_trend": [
                    {
                        "date": item["date"],
                        "sales": item["revenue"],
                        "orders": item["order_count"],
                        "avg_order_value": item["avg_order_value"]
                    }
                    for item in trend_data
                ]
            }
            
        except Exception as e:
            logger.error(f"Failed to get sales trend data: {e}")
            raise HTTPException(status_code=500, detail="Failed to retrieve sales trend data")


    async def get_admin_stats(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get admin dashboard statistics with optional filters"""
        try:
            logger.info(f"📊 Dashboard stats request: date_from={date_from}, date_to={date_to}, status={status}, category={category}")

            # Parse date filters
            today = datetime.now(timezone.utc).date()
            last_month = today - timedelta(days=30)

            # Parse date_from and date_to
            if date_from:
                try:
                    start_date = datetime.fromisoformat(date_from).date()
                except:
                    start_date = last_month
            else:
                start_date = last_month

            if date_to:
                try:
                    end_date = datetime.fromisoformat(date_to).date()
                except:
                    end_date = today
            else:
                end_date = today

            # Compare as explicit UTC datetimes, not bare dates: a bare date cast to timestamptz resolves in the DB session's timezone, which can silently shift the boundary and exclude rows created later "today" in UTC.
            start_date = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
            end_date_exclusive = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

            # Get total users (excluding admin users, filtered by date range)
            total_users = await self.db.scalar(
                select(func.count(User.id)).where(
                    and_(
                        User.role_id.notin_(select(Role.id).where(Role.name.in_(STAFF_ROLE_VALUES))),
                        User.created_at >= start_date,
                        User.created_at < end_date_exclusive
                    )
                )
            )
            logger.info(f"👥 Total users (customers) in date range: {total_users}")

            active_users = await self.db.scalar(
                select(func.count(User.id)).where(
                    and_(
                        User.is_active == True,
                        User.role_id.notin_(select(Role.id).where(Role.name.in_(STAFF_ROLE_VALUES))),
                        User.created_at >= start_date,
                        User.created_at < end_date_exclusive
                    )
                )
            )

            # Validate the status filter once, up front - reused below for both queries
            # so an unrecognized value is skipped consistently everywhere.
            validated_status = None
            if status:
                try:
                    validated_status = OrderStatus(status.lower())
                except ValueError:
                    pass  # Unrecognized status value - skip the filter rather than erroring

            # Get total orders with optional status filter (filtered by date range)
            order_conditions = [
                Order.created_at >= start_date,
                Order.created_at < end_date_exclusive
            ]
            if validated_status:
                order_conditions.append(Order.order_status == validated_status)

            total_orders = await self.db.scalar(
                select(func.count(Order.id)).where(and_(*order_conditions))
            )
            logger.info(f"📦 Total orders (filtered by {status}): {total_orders}")

            orders_today = await self.db.scalar(
                select(func.count(Order.id)).where(
                    func.date(Order.created_at) == today
                )
            )
            logger.info(f"📅 Orders today: {orders_today}")

            # Get total products with optional category filter
            product_conditions = []
            if category:
                product_conditions.append(
                    Product.category_id.in_(
                        category_ids_matching(category)
                    )
                )

            total_products = await self.db.scalar(
                select(func.count(Product.id)).where(and_(*product_conditions)) if product_conditions else select(func.count(Product.id))
            )
            active_products = await self.db.scalar(
                select(func.count(Product.id)).where(Product.product_status == ProductStatus.ACTIVE)
            )

            # Get revenue data (include confirmed, processing, shipped, and delivered orders)
            revenue_conditions = [Order.order_status.in_(['CONFIRMED', 'PROCESSING', 'SHIPPED', 'DELIVERED'])]
            if validated_status:
                revenue_conditions.append(Order.order_status == validated_status)

            total_revenue = await self.db.scalar(
                select(func.coalesce(func.sum(Order.total_amount), 0)).where(and_(*revenue_conditions))
            ) or 0

            revenue_today = await self.db.scalar(
                select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                    and_(
                        Order.order_status.in_(['CONFIRMED', 'PROCESSING', 'SHIPPED', 'DELIVERED']),
                        func.date(Order.created_at) == today
                    )
                )
            ) or 0

            # Calculate average order value
            aov = float(total_revenue) / total_orders if total_orders > 0 else 0

            # Get subscription counts
            total_subscriptions = await self.db.scalar(select(func.count(Subscription.id))) or 0
            active_subscriptions = await self.db.scalar(
                select(func.count(Subscription.id)).where(Subscription.status == 'active')
            ) or 0

            # Build response
            return {
                "users": {
                    "total": total_users or 0,
                    "active": active_users or 0,
                    "new_today": await self.db.scalar(
                        select(func.count(User.id)).where(func.date(User.created_at) == today)
                    ) or 0
                },
                "orders": {
                    "total": total_orders or 0,
                    "today": orders_today or 0,
                    "pending": await self.db.scalar(
                        select(func.count(Order.id)).where(Order.order_status == 'PENDING')
                    ) or 0,
                    "processing": await self.db.scalar(
                        select(func.count(Order.id)).where(Order.order_status == 'PROCESSING')
                    ) or 0,
                    "shipped": await self.db.scalar(
                        select(func.count(Order.id)).where(Order.order_status == 'SHIPPED')
                    ) or 0,
                    "delivered": await self.db.scalar(
                        select(func.count(Order.id)).where(Order.order_status == 'DELIVERED')
                    ) or 0,
                    "cancelled": await self.db.scalar(
                        select(func.count(Order.id)).where(Order.order_status == 'CANCELLED')
                    ) or 0,
                },
                "products": {
                    "total": total_products or 0,
                    "active": active_products or 0,
                    "low_stock": await self.db.scalar(
                        select(func.count(func.distinct(Product.id)))
                        .join(ProductVariant, ProductVariant.product_id == Product.id)
                        .join(Inventory, Inventory.variant_id == ProductVariant.id)
                        .where(
                            and_(
                                Inventory.quantity_available > 0,
                                Inventory.quantity_available <= Inventory.low_stock_threshold
                            )
                        )
                    ) or 0
                },
                "revenue": {
                    "total": float(total_revenue),
                    "today": float(revenue_today),
                    "average_order_value": round(aov, 2)
                },
                "subscriptions": {
                    "total": total_subscriptions,
                    "active": active_subscriptions
                },
                "filters": {
                    "date_from": start_date.isoformat(),
                    "date_to": end_date.isoformat(),
                    "status_filter": status,
                    "category_filter": category
                },
                "generated_at": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            logger.error(f"Failed to get admin stats: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to retrieve admin stats: {str(e)}")

    async def get_admin_overview(self) -> Dict[str, Any]:
        """Get platform overview statistics for admin dashboard"""
        try:
            today = date.today()
            last_30_days = today - timedelta(days=30)

            # User statistics
            total_users = await self.db.scalar(
                select(func.count(User.id)).where(User.role_id.notin_(select(Role.id).where(Role.name.in_(STAFF_ROLE_VALUES))))
            ) or 0
            new_users_30d = await self.db.scalar(
                select(func.count(User.id)).where(
                    and_(User.created_at >= last_30_days, User.role_id.notin_(select(Role.id).where(Role.name.in_(STAFF_ROLE_VALUES))))
                )
            ) or 0

            # Order statistics
            total_orders = await self.db.scalar(select(func.count(Order.id))) or 0
            orders_30d = await self.db.scalar(
                select(func.count(Order.id)).where(Order.created_at >= last_30_days)
            ) or 0

            # Revenue statistics
            total_revenue = await self.db.scalar(
                select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                    Order.order_status.in_(['CONFIRMED', 'PROCESSING', 'SHIPPED', 'DELIVERED'])
                )
            ) or 0
            revenue_30d = await self.db.scalar(
                select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                    and_(
                        Order.created_at >= last_30_days,
                        Order.order_status.in_(['CONFIRMED', 'PROCESSING', 'SHIPPED', 'DELIVERED'])
                    )
                )
            ) or 0

            # Product statistics
            total_products = await self.db.scalar(select(func.count(Product.id))) or 0
            active_products = await self.db.scalar(
                select(func.count(Product.id)).where(Product.product_status == ProductStatus.ACTIVE)
            ) or 0

            # Subscription statistics
            total_subscriptions = await self.db.scalar(select(func.count(Subscription.id))) or 0
            active_subscriptions = await self.db.scalar(
                select(func.count(Subscription.id)).where(Subscription.status == 'active')
            ) or 0

            return {
                "platform_overview": {
                    "total_users": total_users,
                    "new_users_last_30d": new_users_30d,
                    "total_orders": total_orders,
                    "orders_last_30d": orders_30d,
                    "total_revenue": float(total_revenue),
                    "revenue_last_30d": float(revenue_30d),
                    "average_order_value": float(total_revenue / total_orders) if total_orders > 0 else 0,
                    "total_products": total_products,
                    "active_products": active_products,
                    "total_subscriptions": total_subscriptions,
                    "active_subscriptions": active_subscriptions,
                },
                "generated_at": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            logger.error(f"Failed to get admin overview: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to retrieve admin overview: {str(e)}")