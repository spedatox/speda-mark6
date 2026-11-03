"""Explicit registrations for migrated domain capabilities; no engine bootstrap."""
from importlib import import_module

DOMAIN_ROUTERS = ("mail", "reminders", "voice", "telegram", "automations", "academic", "navigation", "aircraft", "files", "health_data", "browser", "web_watch", "news", "hisar")
DOMAIN_SKILLS = {
    "telegram": ("SendTelegramMessageSkill", "SendTelegramFileSkill"),
    "atomix_reports": ("GenerateDailyTrainingProgramSkill",),
    "reminders": ("RemindersSkill",),
    "automations": ("AutomationsSkill",),
    "onyx": ("OnyxSkill",),
    "sandbox": ("RunCommandSkill", "DeliverFileSkill"),
    "solus": ("SolusAccountingSkill",),
    "attendance": ("AttendanceStatusSkill", "AskAttendanceSkill", "RecordAttendanceSkill"),
    "schedule": ("ReadScheduleSkill", "SaveScheduleSkill"),
    "navigation": ("GetRouteSkill", "FindPlacesSkill"),
    "aircraft": ("TrackAircraftSkill",),
    "weather": ("WeatherSkill",),
    "pharmacies": ("OnDutyPharmacySkill",),
    "osint": ("IPGeolocateSkill", "IPReputationSkill", "URLhausLookupSkill", "ThreatFoxLookupSkill",
        "MalwareBazaarLookupSkill", "PwnedPasswordSkill", "DarkWebSearchSkill", "OTXLookupSkill",
        "ShodanLookupSkill", "EmailDiscoverySkill", "CryptoTraceSkill", "IntelXSearchSkill"),
    "save_file": ("SaveFileSkill",),
    "read_skill": ("ReadSkillSkill",),
    "health_data": ("HealthDataSkill",),
    "browser": ("BrowsePageSkill", "BrowserActSkill", "PortalLoginSkill"),
    "news": ("NewsHeadlinesSkill", "NewsWatchSkill", "NewsDeepDiveSkill", "ReadArticleSkill"),
    "hisar": ("HisarSkill",),
    "transit": ("BusArrivalsSkill",),
}


def domain_skills():
    return [getattr(import_module(f"app.skills.{module}"), name)()
            for module, names in DOMAIN_SKILLS.items() for name in names]


def include_routes(app):
    for module in DOMAIN_ROUTERS:
        app.include_router(import_module(f"app.routers.{module}").router)


def domain_tool_names(agent):
    return frozenset(skill.name for skill in domain_skills()
                     if skill.restricted_to is None or agent in skill.restricted_to)
