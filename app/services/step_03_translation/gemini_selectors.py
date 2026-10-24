"""
Cấu hình tập trung toàn bộ Selector cho Google Gemini Web (Playwright).
Ưu tiên sử dụng Semantic Selectors (aria-label, role, visible text) thay vì class CSS dễ thay đổi.
Khi Google cập nhật giao diện web, chỉ cần chỉnh sửa các selector tại file này.
"""

class GeminiSelectors:
    # 1. Ô nhập prompt / tin nhắn
    INPUT_BOX = (
        'rich-textarea div[contenteditable="true"], '
        'div[role="textbox"][contenteditable="true"], '
        'textarea[aria-label*="nhập"], '
        'textarea[aria-label*="prompt"], '
        'div[role="textbox"]'
    )

    # 2. Nút Cuộc trò chuyện mới (New Chat)
    NEW_CHAT_BUTTONS = [
        'a[aria-label*="Cuộc trò chuyện mới"]',
        'button[aria-label*="Cuộc trò chuyện mới"]',
        'a[aria-label*="New chat"]',
        'button[aria-label*="New chat"]',
        'div[role="button"][aria-label*="Cuộc trò chuyện mới"]',
        'div[role="button"][aria-label*="New chat"]',
    ]

    # 3. Nút chọn mô hình (Model Dropdown)
    MODEL_BUTTONS = [
        'button[data-test-id="bard-mode-menu-button"]',
        'bard-mode-switcher button',
        'button.input-area-switch',
        'button[aria-label*="chọn chế độ"]',
        'button[aria-label*="mode picker"]',
        'button:has-text("Gemini Tự động")',
        'button:has-text("Auto")',
    ]

    # 4. Mục menu "Cấp độ tư duy" (Thinking level menu item)
    THINKING_MENU_TEXTS = [
        "Cấp độ tư duy",
        "Thinking level",
        "Tư duy",
        "Thinking"
    ]

    # 5. Các mức tư duy
    THINKING_LEVEL_MAP = {
        "high": ["Cao", "High"],
        "medium": ["Trung bình", "Medium"],
        "low": ["Thấp", "Low"]
    }

    # 6. Selector mục menu chọn model/tư duy
    MENU_ITEMS = [
        'gem-menu-item[role="menuitem"]',
        'div[role="menuitem"]',
        'button[role="menuitem"]',
        'li[role="menuitem"]',
        '[role="menuitem"]',
    ]

    # 7. Nút Gửi (Send Button) - Tuyệt đối không chứa nút Dừng/Stop
    SEND_BUTTONS = [
        'button[aria-label*="Gửi"]:not([aria-label*="Dừng"]):not([aria-label*="Stop"])',
        'button[aria-label*="Send"]:not([aria-label*="Stop"]):not([aria-label*="Dừng"])',
        'button[aria-label*="gửi thông điệp"]:not([aria-label*="Dừng"]):not([aria-label*="Stop"])',
        'button[aria-label*="send message"]:not([aria-label*="Stop"]):not([aria-label*="Dừng"])',
        'button.send-button:not([aria-label*="Dừng"]):not([aria-label*="Stop"])',
    ]

    # 7. Nút Dừng (Stop / Generating Indicator) - Xuất hiện khi AI đang sinh văn bản
    STOP_BUTTONS = [
        'button[aria-label*="Dừng"]',
        'button[aria-label*="Stop"]',
        'button[aria-label*="Dừng tạo"]',
        'button[aria-label*="Stop generating"]',
    ]

    # 8. Khung chứa câu trả lời của mô hình
    RESPONSE_CONTAINERS = [
        'model-response',
        'message-content',
        'div[class*="model-response"]',
        'div[class*="response-content"]',
    ]

    # 9. Nút Sao chép (Copy Button) của câu trả lời
    COPY_BUTTONS = [
        'button[aria-label*="Sao chép"]',
        'button[aria-label*="Copy"]',
    ]

    # 10. Nhận diện trạng thái chưa đăng nhập
    LOGIN_REDIRECT_DOMAINS = [
        "accounts.google.com",
        "signin",
    ]

    SIGN_IN_BUTTONS = [
        'a[href*="accounts.google.com"]',
        'a[aria-label*="Đăng nhập"]',
        'a[aria-label*="Sign in"]',
        'button:has-text("Đăng nhập")',
        'button:has-text("Sign in")',
    ]
