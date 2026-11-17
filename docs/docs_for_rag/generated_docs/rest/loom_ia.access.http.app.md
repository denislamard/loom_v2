# API REST : les routes de loom_ia.access.http.app

## `GET /agents`

Fonction `agents` de `loom_ia.access.http.app`.

```python
@router.get('/agents', tags=['agents'], summary="Agents publiés par l'API")
async def agents(who: Who) -> list[AgentInfo]
```

(sans docstring)

## `POST /agents/{name}/runs`

Fonction `start` de `loom_ia.access.http.app`.

```python
@router.post('/agents/{name}/runs', tags=['runs'], summary='Lance un run et attend sa fin, ou le laisse en arrière-plan', status_code=status.HTTP_201_CREATED, openapi_extra=RUN_BODY, responses={status.HTTP_202_ACCEPTED: {'model': RunAccepted, 'description': "Run inscrit au journal et confié à l'instance"}})
async def start(name: str, request: Request, response: Response, who: Who) -> RunResult | RunAccepted
```

(sans docstring)

## `POST /hooks/{name}`

Fonction `hook` de `loom_ia.access.http.app`.

```python
@router.post('/hooks/{name}', tags=['hooks'], summary="Livraison d'un déclencheur : ouvre le run qu'il déclare", status_code=status.HTTP_202_ACCEPTED, responses={status.HTTP_200_OK: {'model': Triggered, 'description': 'Livraison déjà reçue : son run est retrouvé, pas rouvert'}})
async def hook(name: str, request: Request, response: Response, who: Who) -> Triggered
```

(sans docstring)

## `GET /runs`

Fonction `listed` de `loom_ia.access.http.app`.

```python
@router.get('/runs', tags=['journal'], summary="Runs du client, le plus récent d'abord")
async def listed(who: Who, agent: str | None=None, statuses: Annotated[tuple[RunStatus, ...], Query(alias='status')]=(), since: AwareDatetime | None=None, until: AwareDatetime | None=None, limit: Annotated[int, Query(ge=1, le=RUNS_MAX)]=RUNS_LIMIT, sessions: Annotated[int, Query(ge=1, le=SESSIONS_MAX)]=SESSIONS_READ) -> RunPage
```

(sans docstring)

## `GET /events`

Fonction `found_events` de `loom_ia.access.http.app`.

```python
@router.get('/events', tags=['journal'], summary="Recherche d'événements au journal", response_model=list[Event])
async def found_events(who: Who, session_id: SessionId | None=None, run_id: RunId | None=None, types: Annotated[tuple[str, ...], Query(alias='type')]=(), categories: Annotated[tuple[EventCategory, ...], Query(alias='category')]=(), statuses: Annotated[tuple[EventStatus, ...], Query(alias='status')]=(), agent: str | None=None, role: str | None=None, tool_name: str | None=None, model_id: str | None=None, model: str | None=None, since: AwareDatetime | None=None, until: AwareDatetime | None=None, after: Annotated[EventId | None, Query(json_schema_extra={'pattern': EVENT_ID_PATTERN})]=None, limit: Annotated[int, Query(ge=1, le=EVENTS_MAX)]=EVENTS_LIMIT) -> Response | list[Event]
```

(sans docstring)

## `GET /traces/{run_id}`

Fonction `traced` de `loom_ia.access.http.app`.

```python
@router.get('/traces/{run_id}', tags=['journal'], summary="Trace d'un run : ses spans, sous-runs compris")
async def traced(who: Who, run_id: RunId, session_id: SessionId | None=None) -> Trace
```

(sans docstring)

## `GET /runs/{run_id}`

Fonction `result` de `loom_ia.access.http.app`.

```python
@router.get('/runs/{run_id}', tags=['runs'], summary="Statut et résultat d'un run")
async def result(who: Who, run_id: RunId, session_id: SessionId | None=None) -> RunResult
```

(sans docstring)

## `GET /runs/{run_id}/events`

Fonction `events` de `loom_ia.access.http.app`.

```python
@router.get('/runs/{run_id}/events', tags=['runs'], summary='Journal du run en SSE')
async def events(request: Request, who: Who, run_id: RunId, session_id: SessionId | None=None, after_seq: Annotated[int, Query(ge=0)]=0, subruns: bool=True) -> EventSourceResponse
```

(sans docstring)

## `POST /runs/{run_id}/approve`

Fonction `approve` de `loom_ia.access.http.app`.

```python
@router.post('/runs/{run_id}/approve', tags=['runs'], summary='Autorise un appel que le run attend')
async def approve(who: Who, run_id: RunId, body: Approval, session_id: SessionId | None=None) -> Decided
```

(sans docstring)

## `POST /runs/{run_id}/reject`

Fonction `reject` de `loom_ia.access.http.app`.

```python
@router.post('/runs/{run_id}/reject', tags=['runs'], summary='Refuse un appel que le run attend')
async def reject(who: Who, run_id: RunId, body: Decision, session_id: SessionId | None=None) -> Decided
```

(sans docstring)

## `POST /runs/{run_id}/answer`

Fonction `answer` de `loom_ia.access.http.app`.

```python
@router.post('/runs/{run_id}/answer', tags=['runs'], summary='Répond à une question que le run attend')
async def answer(who: Who, run_id: RunId, body: Answer, session_id: SessionId | None=None) -> Answered
```

(sans docstring)

## `POST /runs/{run_id}/cancel`

Fonction `cancel` de `loom_ia.access.http.app`.

```python
@router.post('/runs/{run_id}/cancel', tags=['runs'], summary='Arrête un run')
async def cancel(who: Who, run_id: RunId, body: Annotated[Cancellation | None, Body()]=None, session_id: SessionId | None=None) -> Cancelled
```

(sans docstring)

## `GET /sessions`

Fonction `sessions` de `loom_ia.access.http.app`.

```python
@router.get('/sessions', tags=['sessions'], summary="Sessions du client, la plus récente d'abord")
async def sessions(who: Who) -> list[SessionRecord]
```

(sans docstring)

## `GET /sessions/{session_id}`

Fonction `session` de `loom_ia.access.http.app`.

```python
@router.get('/sessions/{session_id}', tags=['sessions'], summary="Fiche d'une session")
async def session(who: Who, session_id: SessionId) -> SessionInfo
```

(sans docstring)

## `GET /sessions/{session_id}/events`

Fonction `export` de `loom_ia.access.http.app`.

```python
@router.get('/sessions/{session_id}/events', tags=['sessions'], summary="Journal d'une session en JSONL")
async def export(who: Who, session_id: SessionId) -> Response
```

(sans docstring)

## `GET /sessions/{session_id}/files`

Fonction `files` de `loom_ia.access.http.app`.

```python
@router.get('/sessions/{session_id}/files', tags=['sessions'], summary="Fichiers d'une session")
async def files(who: Who, session_id: SessionId, origins: Annotated[tuple[ArtifactOrigin, ...], Query(alias='origin')]=()) -> list[SessionFile]
```

(sans docstring)

## `GET /sessions/{session_id}/files/{file}`

Fonction `download` de `loom_ia.access.http.app`.

```python
@router.get('/sessions/{session_id}/files/{file}', tags=['sessions'], summary="Octets d'un fichier d'une session", response_class=Response)
async def download(who: Who, session_id: SessionId, file: str) -> Response
```

(sans docstring)

## `DELETE /sessions/{session_id}`

Fonction `forget` de `loom_ia.access.http.app`.

```python
@router.delete('/sessions/{session_id}', tags=['sessions'], summary='Supprime une session (RGPD)')
async def forget(who: Who, session_id: SessionId) -> SessionDeletion
```

(sans docstring)

## `GET /sessions/{session_id}/report`

Fonction `report` de `loom_ia.access.http.app`.

```python
@router.get('/sessions/{session_id}/report', tags=['sessions'], summary="Consommation d'une session")
async def report(who: Who, session_id: SessionId) -> UsageReport
```

(sans docstring)
