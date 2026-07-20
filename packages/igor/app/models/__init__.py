# Import all models so Base.metadata is fully populated when init_db() calls create_all.
from app.models.user import User
from app.models.session import Session
from app.models.message import Message
from app.models.memory import Memory
from app.models.memory_file import MemoryFile
from app.models.memory_revision import MemoryRevision
from app.models.message_embedding import MessageEmbedding
from app.models.agent import AgentRecord
from app.models.agent_message import AgentMessage
from app.models.tool_call import ToolCall
from app.models.notification import Notification
from app.models.automation import Automation
from app.models.news_item import NewsItem
from app.models.news_watch import NewsWatch
from app.models.news_quota import NewsQuota
from app.models.health_sample import HealthSample, HealthDaily

__all__ = [
    "User", "Session", "Message", "Memory", "MemoryFile", "MemoryRevision",
    "MessageEmbedding",
    "AgentRecord", "AgentMessage", "ToolCall", "Notification", "Automation",
    "NewsItem", "NewsWatch", "NewsQuota",
    "HealthSample", "HealthDaily",
]
