"""HTTP 路由分组。

app.py 原来把 35 个路由平铺在一个 1862 行的文件里，加端点要在大海捞针。
按域拆成 router 后：加一个接口 = 在对应模块里加一个 @router 函数 + 这里
加一行注册；app.py 只留装配、异常处理、中间件和少数根级路由。

接入新分组时记得在 register() 里登记，否则路由不生效（静默 404）。
"""
from __future__ import annotations

from fastapi import FastAPI

from core.routes import convert as convert_routes
from core.routes import dashboards as dashboards_routes
from core.routes import db as db_routes
from core.routes import export as export_routes
from core.routes import generate as generate_routes
from core.routes import history as history_routes


def register(app: FastAPI) -> None:
    app.include_router(db_routes.router)
    app.include_router(convert_routes.router)
    app.include_router(generate_routes.router)
    app.include_router(dashboards_routes.router)
    app.include_router(history_routes.router)
    app.include_router(export_routes.router)
