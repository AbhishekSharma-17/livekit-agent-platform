"""Test chat over the LiveKit room (V3-02, AGENT-ACCESS D-V3-3 and §4.7).

``transport`` holds the room connection (``RoomTransport`` Protocol and its
``livekit.rtc`` implementation), ``manager`` the chats (limits, idle timeout,
turn numbering, the session-event tail) and ``tools`` the four MCP tools
(``chat_start``, ``chat_send``, ``chat_rewind``, ``chat_end``).
"""
