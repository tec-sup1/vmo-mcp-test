"""MCP test server cho Render — kiểm tra ChatGPT Plus có gọi được tool MCP không.
Không cần key Odoo, không chạm dữ liệu. Bind 0.0.0.0:$PORT theo yêu cầu của Render."""

import os
from fastmcp import FastMCP

mcp = FastMCP("VietMRO Test")


@mcp.tool
def ping() -> str:
    """Kiểm tra kết nối. Trả về 'pong' nếu MCP chạy."""
    return "pong — MCP server VietMRO (Render) đang chạy OK! ChatGPT đã gọi tool thành công."


@mcp.tool
def echo(text: str) -> str:
    """Lặp lại văn bản (kiểm tra truyền tham số)."""
    return f"Bạn vừa gửi: {text}"


@mcp.tool
def test_info() -> dict:
    """Thông tin server test."""
    return {"server": "VietMRO MCP Test (Render)", "status": "ok",
            "message": "Thấy cái này nghĩa là ChatGPT Plus gọi được tool MCP."}


@mcp.tool
def search(query: str) -> list:
    """Tìm kiếm (stub test)."""
    return [{"id": "1", "title": f"Kết quả test cho: {query}", "url": "https://vietmro.com"}]


@mcp.tool
def fetch(id: str) -> dict:
    """Lấy nội dung theo id (stub test)."""
    return {"id": id, "title": "Test", "text": "Nội dung test từ MCP server VietMRO.",
            "url": "https://vietmro.com"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8777"))
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")
