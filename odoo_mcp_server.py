"""
Odoo MCP Server (bản Render) — bắc cầu ChatGPT (hoặc client MCP bất kỳ) sang Odoo External API.

Thiết kế cốt lõi:
- STATELESS về credential: mỗi request mang token = "login:apikey" của CHÍNH người
  dùng, gửi qua header `Authorization: Bearer`. Server KHÔNG giữ API key dùng chung.
- Mọi lời gọi Odoo chạy dưới danh tính user đó → Odoo tự áp quyền. Mỗi phòng chỉ
  thấy đúng dữ liệu mình được phép.
- Transport: streamable HTTP tại /mcp. Bind 0.0.0.0:$PORT theo yêu cầu của Render.
- CHỈ ĐỌC (search_read/read/count/...). Không có tool ghi → an toàn khi bàn giao.

Biến môi trường (đặt trên Render):
  ODOO_URL   mặc định https://erp.vietmro.com
  ODOO_DB    BẮT BUỘC — tên database Odoo production (vd vmoerp_prod)
  PORT       Render tự cấp (fallback 8000)
"""

import os
import threading
import xmlrpc.client
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

ODOO_URL = os.environ.get("ODOO_URL", "https://erp.vietmro.com").rstrip("/")
ODOO_DB = os.environ.get("ODOO_DB", "").strip()

if not ODOO_DB:
    raise RuntimeError(
        "Thiếu biến môi trường ODOO_DB (tên database Odoo). "
        "Trên Render: Environment → Add → ODOO_DB = vmoerp_prod."
    )

mcp = FastMCP("Odoo VietMRO")

# Cache uid theo (login, apikey) để khỏi authenticate lại mỗi lần gọi tool.
_uid_cache: dict[tuple[str, str], int] = {}
_cache_lock = threading.Lock()


class AuthError(Exception):
    """Lỗi xác thực token — trả về client với thông điệp rõ ràng."""


def _parse_token() -> tuple[str, str]:
    """Lấy (login, apikey) từ header của request HTTP hiện tại.

    Chấp nhận:
      Authorization: Bearer <login>:<apikey>
      Authorization: <login>:<apikey>
      X-Odoo-Token: <login>:<apikey>
    """
    req = get_http_request()
    raw = req.headers.get("authorization") or req.headers.get("x-odoo-token") or ""
    if raw.lower().startswith("bearer "):
        raw = raw[7:]
    raw = raw.strip()
    if not raw or ":" not in raw:
        raise AuthError(
            "Thiếu hoặc sai token. Token phải có dạng 'login:apikey' "
            "(ví dụ nv107@vietmro.com:xxxxxxxxxxxx), gửi qua Authorization: Bearer."
        )
    login, apikey = raw.split(":", 1)
    login, apikey = login.strip(), apikey.strip()
    if not login or not apikey:
        raise AuthError("Token thiếu login hoặc apikey.")
    return login, apikey


def _auth() -> tuple[int, Any, str]:
    """Xác thực với Odoo bằng token của request, trả về (uid, proxy_object, apikey)."""
    login, apikey = _parse_token()
    cache_key = (login, apikey)

    with _cache_lock:
        uid = _uid_cache.get(cache_key)

    if uid is None:
        common = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/common", allow_none=True
        )
        uid = common.authenticate(ODOO_DB, login, apikey, {})
        if not uid:
            raise AuthError(
                f"Đăng nhập Odoo thất bại cho '{login}'. "
                "Kiểm tra tên DB, login và API key."
            )
        with _cache_lock:
            _uid_cache[cache_key] = uid

    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
    return uid, models, apikey


def _call(model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
    """Gọi execute_kw dưới danh tính của user trong token."""
    uid, models, apikey = _auth()
    return models.execute_kw(ODOO_DB, uid, apikey, model, method, args, kwargs or {})


# ───────────────────────────── TOOLS ─────────────────────────────

@mcp.tool
def ping() -> str:
    """Kiểm tra server còn sống (KHÔNG cần token). Trả về 'pong'."""
    return "pong — Odoo MCP server VietMRO đang chạy. Dùng 'whoami' để kiểm tra token."


@mcp.tool
def whoami() -> dict:
    """Xác nhận danh tính Odoo gắn với token hiện tại (uid, tên, login, công ty).

    Dùng để kiểm tra kết nối và biết mình đang đăng nhập bằng tài khoản nào.
    """
    uid, models, apikey = _auth()
    info = models.execute_kw(
        ODOO_DB, uid, apikey, "res.users", "read",
        [[uid]], {"fields": ["name", "login", "company_id"]},
    )
    return {
        "uid": uid,
        "user": info[0] if info else None,
        "odoo_url": ODOO_URL,
        "db": ODOO_DB,
    }


@mcp.tool
def odoo_search_read(
    model: str,
    domain: list | None = None,
    fields: list | None = None,
    limit: int = 50,
    offset: int = 0,
    order: str | None = None,
) -> list:
    """Tra cứu bản ghi Odoo (search + read một lượt).

    model:  tên model, vd 'product.template', 'res.partner', 'sale.order', 'stock.quant'.
    domain: điều kiện lọc kiểu Odoo, vd [["name","ilike","van bi"]] hoặc
            [["list_price",">",100],["sale_ok","=",true]]. Bỏ trống = tất cả (bị giới hạn bởi limit).
    fields: danh sách field cần lấy, vd ["name","default_code","list_price","qty_available"].
            Bỏ trống = Odoo trả field mặc định.
    limit:  số bản ghi tối đa (mặc định 50). offset: bỏ qua N bản ghi đầu (phân trang).
    order:  sắp xếp, vd "list_price desc" hoặc "create_date desc".
    """
    kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
    if fields:
        kwargs["fields"] = fields
    if order:
        kwargs["order"] = order
    return _call(model, "search_read", [domain or []], kwargs)


@mcp.tool
def odoo_read(model: str, ids: list, fields: list | None = None) -> list:
    """Đọc chi tiết các bản ghi theo danh sách id."""
    kwargs = {"fields": fields} if fields else {}
    return _call(model, "read", [ids], kwargs)


@mcp.tool
def odoo_search_count(model: str, domain: list | None = None) -> int:
    """Đếm số bản ghi khớp điều kiện (nhanh, không tải dữ liệu)."""
    return _call(model, "search_count", [domain or []])


@mcp.tool
def odoo_name_search(model: str, name: str, limit: int = 20) -> list:
    """Tìm nhanh theo tên, trả về [[id, display_name], ...]. Hữu ích để lấy id trước khi read."""
    return _call(model, "name_search", [name], {"limit": limit})


@mcp.tool
def odoo_fields_get(model: str, attributes: list | None = None) -> dict:
    """Liệt kê các field của model (để biết đúng tên field mà tra cứu).

    attributes mặc định: ['string','type','help','relation'].
    """
    attrs = attributes or ["string", "type", "help", "relation"]
    return _call(model, "fields_get", [], {"attributes": attrs})


@mcp.tool
def odoo_list_models(filter_name: str | None = None, limit: int = 100) -> list:
    """Liệt kê các model có trong Odoo (field 'model' và 'name').

    filter_name: lọc theo tên kỹ thuật, vd 'product' hoặc 'sale'. Bỏ trống = tất cả (giới hạn limit).
    """
    domain = [["model", "ilike", filter_name]] if filter_name else []
    return _call(
        "ir.model", "search_read", [domain],
        {"fields": ["model", "name"], "limit": limit, "order": "model"},
    )


if __name__ == "__main__":
    # Render cấp $PORT và yêu cầu bind 0.0.0.0.
    port = int(os.environ.get("PORT", os.environ.get("MCP_PORT", "8000")))
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    mcp.run(transport="http", host=host, port=port, path="/mcp")
