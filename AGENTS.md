# AGENTS.md — gpt_signup_hybrid

Hướng dẫn cho coding agent (Kiro / Codex / Claude / khác) khi làm việc trong repo này.

## Ngôn ngữ + style

- Trả lời tiếng Việt, ngắn gọn, đi thẳng vấn đề.
- Không tổng kết dài dòng. Không tạo doc/markdown khi user không yêu cầu.

## File layout

- File test/debug → `test/`
- Tài liệu .md user yêu cầu → `docs/`
- Không tạo file tạm ngoài 2 chỗ trên.

## Verify / Debug / Run

- **Cấm** dùng inline `python3 -c "..."`, `node -e "..."`, `bash -c "..."`, `eval` để verify hay debug.
- Mọi check (syntax, import, smoke, repro bug) phải nằm trong file `.py`/`.js`/`.sh` thật ở `test/`.
- Đặt tên rõ ràng:
  - `test/syntax_check.py` — parse AST mọi file Python.
  - `test/check_<scope>.py` — check chức năng cụ thể.
  - `test/smoke_<scope>.py` — smoke test integration.
  - `test/test_<scope>.py` — unit test.
- Chỉ chạy file vừa viết: `python3 test/<file>.py`. Không chạy script ad-hoc rồi xóa.

## Code rules

- Kiến trúc tổng thể, không feature rời rạc. SOLID, DRY, Fail-Fast.
- Không hardcode default insecure (TLS verify off, CORS *, auth bypass) — phải opt-in qua flag/env.
- Không fallback che lỗi. Không code chết sau `return`/`raise`.
- Không viết test khi user không yêu cầu (trừ test verify ở mục trên).

## Quy ước

- User nói "tiếp tục" → làm tiếp theo best practice, không hỏi lại.
- Yêu cầu mơ hồ → dừng, hỏi rõ với options cụ thể.
- Thấy rủi ro hoặc cách tốt hơn → nói thẳng.

## Tool ưu tiên

- Tra docs → Context7 MCP
- Test web UI → Playwright MCP
