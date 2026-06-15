EDITED_MESSAGE_FORMAT = (
    '<b>👤 ✏️ [ <a href="tg://user?id={user_id}">{user_fullname_escaped}</a> ] '
    '<code>{user_id}</code></b>\n'
    'Message from {timestamp}\n\n'
    '<b>Changed from:</b>\n'
    '<blockquote><code>{old_text}</code></blockquote>\n'
    '<b>To:</b>\n'
    '<blockquote><code>{new_text}</code></blockquote>'
)

DELETED_MESSAGE_FORMAT = (
    '<b>👤 🗑 [ <a href="tg://user?id={user_id}">{user_fullname_escaped}</a> ] '
    '<code>{user_id}</code></b>\n'
    'Message from {timestamp}\n\n'
    '<b>Deleted:</b>\n'
    '<blockquote><code>{old_text}</code></blockquote>'
)

NEW_USER_MESSAGE_FORMAT = (
    '<b>👤 📡 [ <a href="tg://user?id={user_id}">{user_fullname_escaped}</a> ]</b>\n\n'
    '<b>🆔 ID: </b><code>{user_id}</code>'
)
