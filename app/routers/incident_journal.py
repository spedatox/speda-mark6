"""Owner API for the Incident Journal in Iacon."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, Field, StringConstraints

from app.services.incident_journal import JournalView

router = APIRouter(prefix="/iacon/hall-of-records/incidents", tags=["iacon"])
IncidentId = Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")]
Severity = Literal["critical", "error", "warning", "test"]


class IncidentSelection(BaseModel):
    ids: list[Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]] | None = Field(
        default=None, min_length=1, max_length=1000)
    reviewed: bool = False
    severity: Severity | None = None
    q: str = Field("", max_length=120)

    def scope(self):
        return dict(ids=self.ids, reviewed=self.reviewed, severity=self.severity, search=self.q.strip().lower())


class BulkReview(IncidentSelection):
    mark_reviewed: bool


def view(request: Request):
    journal = request.app.state.damage_control_journal
    if journal is None:
        raise HTTPException(503, "The incident journal has not been initialized")
    return JournalView(journal)


def found(record):
    if record is None:
        raise HTTPException(404, "Incident not found")
    return record


@router.get("")
def list_incidents(response: Response, journal: Annotated[JournalView, Depends(view)],
                   reviewed: bool = False, severity: Severity | None = None,
                   q: str = Query("", max_length=120), offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)):
    response.headers["Cache-Control"] = "no-store"
    return journal.list(reviewed=reviewed, severity=severity, search=q.strip().lower(), limit=limit, offset=offset)


@router.post("/export")
def bulk_export(body: IncidentSelection, journal: Annotated[JournalView, Depends(view)]):
    try:
        pdf = journal.bulk_pdf(**body.scope())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if pdf is None:
        raise HTTPException(404, "No incidents match this export")
    return Response(pdf, media_type="application/pdf", headers={
        "Cache-Control": "no-store", "Content-Disposition": 'attachment; filename="damage-control-journal.pdf"',
    })


@router.post("/selection")
def select_incidents(body: IncidentSelection, response: Response, journal: Annotated[JournalView, Depends(view)]):
    response.headers["Cache-Control"] = "no-store"
    try:
        return journal.selection(**body.scope())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/review")
def review_incidents(body: BulkReview, journal: Annotated[JournalView, Depends(view)]):
    try:
        return journal.review_selection(mark_reviewed=body.mark_reviewed, **body.scope())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{identity}")
def get_incident(identity: IncidentId, response: Response, journal: Annotated[JournalView, Depends(view)]):
    response.headers["Cache-Control"] = "no-store"
    return found(journal.detail(identity))


@router.put("/{identity}/review")
def mark_reviewed(identity: IncidentId, journal: Annotated[JournalView, Depends(view)]):
    return found(journal.review(identity))


@router.post("/{identity}/assessment")
async def request_assessment(identity: IncidentId, request: Request, journal: Annotated[JournalView, Depends(view)]):
    return found(await journal.assess(identity, request.app.state.orchestrator.analyse_incident))


@router.get("/{identity}/pdf")
def export_pdf(identity: IncidentId, journal: Annotated[JournalView, Depends(view)]):
    return Response(found(journal.pdf(identity)), media_type="application/pdf", headers={
        "Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="damage-control-{identity}.pdf"',
    })
