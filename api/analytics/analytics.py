"""Analytics API: e-commerce metrics (conversion, cart abandonment, refunds, repeat customers)."""
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID
from core.logging import get_structured_logger as get_logger

from core.db import get_db
from core.dependencies import get_current_auth_user, require_admin
from core.utils.response import Response
from models.accounts.user import User
from models.system import EventType
from models.accounts import TrafficSource
from services.analytics.analytics import AnalyticsService
from services.commerce.orders import OrderService
from core.exceptions import APIException
from core.dependencies import get_current_auth_user, require_admin

logger = get_logger(__name__)
router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/track/")
async def track(
    event_data: dict,
    current_user: Optional[User] = Depends(get_current_auth_user),
    db: AsyncSession = Depends(get_db)
):
    """Track an analytics event; used by the frontend for user interactions."""
    try:
        analytics_service = AnalyticsService(db)
        event = await analytics_service.track_event(
            session_id=event_data.get("session_id"),
            event_type=EventType(event_data.get("event_type")),
            user_id=current_user.id if current_user else None,
            event_data=event_data.get("data", {}),
            page_url=event_data.get("page_url"),
            page_title=event_data.get("page_title"),
            order_id=UUID(event_data["order_id"]) if event_data.get("order_id") else None,
            product_id=UUID(event_data["product_id"]) if event_data.get("product_id") else None,
            revenue=event_data.get("revenue"),
            session_info=event_data.get("session"),
        )
        
        return Response.success(
            data={"event_id": str(event.id)},
            message="Event tracked successfully"
        )
        
    except ValueError as e:
        raise APIException(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"Invalid event data: {str(e)}"
        )
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to track event: {str(e)}"
        )


@router.get("/conversion-rates/")
async def conversion_rates(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    traffic_source: Optional[TrafficSource] = Query(None),
    days: Optional[int] = Query(30),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get conversion rate metrics."""
    try:
        analytics_service = AnalyticsService(db)
        # Set default date range if not provided
        if not end_date:
            end_date = datetime.now(timezone.utc)
        if not start_date:
            start_date = end_date - timedelta(days=days)
        
        metrics = await analytics_service.get_conversion_metrics(
            start_date=start_date,
            end_date=end_date,
            traffic_source=traffic_source
        )
        
        return Response.success(
            data=metrics,
            message="Conversion metrics retrieved successfully"
        )
        
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to retrieve conversion metrics: {str(e)}"
        )


@router.get("/cart-abandonment/")
async def cart_abandonment(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    days: Optional[int] = Query(30),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get cart abandonment metrics."""
    try:
        analytics_service = AnalyticsService(db)
        # Set default date range if not provided
        if not end_date:
            end_date = datetime.now(timezone.utc)
        if not start_date:
            start_date = end_date - timedelta(days=days)
        
        metrics = await analytics_service.get_cart_abandonment_metrics(
            start_date=start_date,
            end_date=end_date
        )
        
        return Response.success(
            data=metrics,
            message="Cart abandonment metrics retrieved successfully"
        )
        
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to retrieve cart abandonment metrics: {str(e)}"
        )


@router.get("/time-to-purchase/")
async def time_to_purchase(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    days: Optional[int] = Query(30),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get time to first purchase metrics."""
    try:
        analytics_service = AnalyticsService(db)
        # Set default date range if not provided
        if not end_date:
            end_date = datetime.now(timezone.utc)
        if not start_date:
            start_date = end_date - timedelta(days=days)
        
        metrics = await analytics_service.get_time_to_purchase_metrics(
            start_date=start_date,
            end_date=end_date
        )
        
        return Response.success(
            data=metrics,
            message="Time to purchase metrics retrieved successfully"
        )
        
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to retrieve time to purchase metrics: {str(e)}"
        )


@router.get("/refund-rates/")
async def refund_rates(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    days: Optional[int] = Query(30),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get refund rate metrics."""
    try:
        analytics_service = AnalyticsService(db)
        # Set default date range if not provided
        if not end_date:
            end_date = datetime.now(timezone.utc)
        if not start_date:
            start_date = end_date - timedelta(days=days)
        
        metrics = await analytics_service.get_refund_rate_metrics(
            start_date=start_date,
            end_date=end_date
        )
        
        return Response.success(
            data=metrics,
            message="Refund rate metrics retrieved successfully"
        )
        
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to retrieve refund rate metrics: {str(e)}"
        )


@router.get("/repeat-customers/")
async def repeat_customers(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    days: Optional[int] = Query(30),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get repeat customer metrics."""
    try:
        analytics_service = AnalyticsService(db)
        # Set default date range if not provided
        if not end_date:
            end_date = datetime.now(timezone.utc)
        if not start_date:
            start_date = end_date - timedelta(days=days)
        
        metrics = await analytics_service.get_repeat_customer_metrics(
            start_date=start_date,
            end_date=end_date
        )
        
        return Response.success(
            data=metrics,
            message="Repeat customer metrics retrieved successfully"
        )
        
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to retrieve repeat customer metrics: {str(e)}"
        )


@router.get("/sales-trend/")
async def sales_trend(
    days: int = Query(30),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get sales trend data over specified number of days."""
    try:
        analytics_service = AnalyticsService(db)
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)

        trend_data = await analytics_service.get_sales_trend_data(start_date=start_date, end_date=end_date)

        return Response.success(
            data=trend_data,
            message="Sales trend data retrieved successfully"
        )

    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to retrieve sales trend data: {str(e)}"
        )

@router.get("/users-growth-trend/")
async def users_growth_trend(
    days: int = Query(30),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get users growth trend data over specified number of days."""
    try:
        analytics_service = AnalyticsService(db)
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)

        trend_data = await analytics_service.get_users_growth_trend(start_date=start_date, end_date=end_date)

        return Response.success(
            data=trend_data,
            message="Users growth trend data retrieved successfully"
        )

    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to retrieve users growth trend data: {str(e)}"
        )


@router.get("/kpis/")
async def kpis(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    days: Optional[int] = Query(7),
    compare_previous: bool = Query(True),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get key performance indicators (KPIs)."""
    try:
        analytics_service = AnalyticsService(db)
        # Set default date range if not provided
        if not end_date:
            end_date = datetime.now(timezone.utc)
        if not start_date:
            start_date = end_date - timedelta(days=days)
        
        # Get current period data
        current_data = await analytics_service.get_comprehensive_dashboard_data(
            start_date=start_date,
            end_date=end_date
        )
        
        kpis = {
            "period": current_data["period"],
            "kpis": {
                "conversion_rate": current_data["conversion"]["overall"]["conversion_rate"],
                "cart_abandonment_rate": current_data["cart_abandonment"]["abandonment_rates"]["overall_abandonment_rate"],
                "average_order_value": current_data["conversion"]["overall"]["average_order_value"],
                "refund_rate": current_data["refunds"]["overall"]["refund_rate"],
                "repeat_customer_rate": current_data["repeat_customers"]["overall"]["repeat_rate"],
                "total_revenue": current_data["conversion"]["overall"]["total_revenue"],
                "total_orders": current_data["refunds"]["overall"]["total_orders"],
                "avg_time_to_first_purchase_days": current_data["time_to_purchase"]["metrics"]["average_days"]
            }
        }
        
        # Add comparison with previous period if requested
        if compare_previous:
            period_length = end_date - start_date
            prev_end_date = start_date
            prev_start_date = prev_end_date - period_length
            
            try:
                previous_data = await analytics_service.get_comprehensive_dashboard_data(
                    start_date=prev_start_date,
                    end_date=prev_end_date
                )
                
                # Calculate percentage changes
                def calculate_change(current, previous):
                    if previous == 0:
                        return 0 if current == 0 else 100
                    return round(((current - previous) / previous) * 100, 2)
                
                kpis["comparison"] = {
                    "previous_period": {
                        "start_date": prev_start_date.isoformat(),
                        "end_date": prev_end_date.isoformat()
                    },
                    "changes": {
                        "conversion_rate": calculate_change(
                            current_data["conversion"]["overall"]["conversion_rate"],
                            previous_data["conversion"]["overall"]["conversion_rate"]
                        ),
                        "cart_abandonment_rate": calculate_change(
                            current_data["cart_abandonment"]["abandonment_rates"]["overall_abandonment_rate"],
                            previous_data["cart_abandonment"]["abandonment_rates"]["overall_abandonment_rate"]
                        ),
                        "average_order_value": calculate_change(
                            current_data["conversion"]["overall"]["average_order_value"],
                            previous_data["conversion"]["overall"]["average_order_value"]
                        ),
                        "refund_rate": calculate_change(
                            current_data["refunds"]["overall"]["refund_rate"],
                            previous_data["refunds"]["overall"]["refund_rate"]
                        ),
                        "repeat_customer_rate": calculate_change(
                            current_data["repeat_customers"]["overall"]["repeat_rate"],
                            previous_data["repeat_customers"]["overall"]["repeat_rate"]
                        ),
                        "total_revenue": calculate_change(
                            current_data["conversion"]["overall"]["total_revenue"],
                            previous_data["conversion"]["overall"]["total_revenue"]
                        )
                    }
                }
            except Exception as e:
                logger.warning(f"Failed to get comparison data: {e}")
                kpis["comparison"] = {"error": "Comparison data unavailable"}
        
        return Response.success(
            data=kpis,
            message="KPIs retrieved successfully"
        )
        
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to retrieve KPIs: {str(e)}"
        )


@router.get("/stats/")
async def admin_stats(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    order_status: Optional[str] = Query(None, alias="status"),
    category: Optional[str] = Query(None),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get admin dashboard statistics with filters."""
    try:
        analytics_service = AnalyticsService(db)
        stats = await analytics_service.get_admin_stats(
            date_from=date_from,
            date_to=date_to,
            status=order_status,
            category=category
        )
        return Response.success(data=stats)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch admin stats: {str(e)}"
        )


@router.get("/dashboard/admin/")
async def admin_dashboard(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    order_status: Optional[str] = Query(None, alias="status"),
    category: Optional[str] = Query(None),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get comprehensive admin dashboard data."""
    try:
        analytics_service = AnalyticsService(db)
        overview = await analytics_service.get_admin_overview()
        return Response.success(data=overview)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch dashboard data: {str(e)}"
        )
