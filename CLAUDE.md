# Time to Harvest (OxHack26)

Forecast CSV → value function (£/ha per field per hour) → planner (which field each combine works each hour) → replan every time a new moisture reading arrives → £ earned. Bare-bones, beginner-friendly Python: plain functions, numpy arrays and dicts.

## Commands (always through uv)
- Install: `uv sync`
- Check, run before saying "done": `uv run ruff check . && uv run pytest -q`
- Format: `uv run ruff format .`
- Live run, today's forecast: `uv run python run.py` (prints results and opens a matplotlib window)
- Replay past dates with real past forecasts: `uv run python replay.py 2026-10-10 2026-10-25 [planner]` (needs internet the first time)
- Compare planners on past dates: `uv run python compare.py 2026-10-10 2026-10-25` (every `plan_*` in `harvest/planners/`, same data as replay)
- Presentation charts for a whole season: `uv run python season.py` (rollout_start to rollout_end in `params.yaml`, replayed with mpc and farmer, results saved and reused; money, money gap and plan PNGs in `outputs/season/`)
- Fresh forecast: `uv run python data/download_forecast.py`
- Web app: `uv run --group web uvicorn webapp.server:app`, then open http://localhost:8000 (restart it after Python changes). Its tests: `uv run pytest webapp/tests -q`
- Farmer site: `uv run --group web uvicorn farmer.server:app --port 8001`, then open http://localhost:8001. Its tests: `uv run pytest farmer/tests -q`

## Map
- `params.yaml`: every number. Three modes: `run.py` (live: today's forecast, one member as the truth), `replay.py` (one planner on past dates), `compare.py` (every planner on past dates, plus a side-by-side window). Their settings are grouped in `params.yaml` the same way.
- `harvest/forecast.py` → `value.py` → `planners/`, looped by `simulate.py`. **Inputs, outputs and array shapes of every function: `harvest/README.md`.**
- Past dates: `harvest/past_data.py` (`past_rollout`) fetches the past forecast runs (dynamical.org, saved in `data/past_forecasts/`) and the weather that happened (Open-Meteo).
- `scripts/`: helper scripts run by hand (`uv run python scripts/<name>.py`): `moisture_forecast.py` makes `outputs/moisture_forecast.csv` for `run.py`; `plot_*.py` and `moisture_sensitivity.py` save charts to `outputs/`; `rain_sensitivity.py` prints how much the rain settings matter.
- `data/forecast.csv`: prediction data, format in `data/README.md`. `tests/test_pipeline.py`: all tests.
- `webapp/`: the farmer's control panel (FastAPI + plain HTML/JS, details in `webapp/README.md`). Only `webapp/bridge.py` imports `harvest/`. **For anything in `webapp/static/`, follow `webapp/DESIGN.md`.**
- `farmer/`: the website for farmers (the demo's look, few words, real weather maps, pretend controls; details in `farmer/README.md`). Only `farmer/source.py` imports the rest of the repo. **For anything in `farmer/static/`, follow `farmer/DESIGN.md`.**

## Ownership (fill in names)
| Area | Files | Owner |
|---|---|---|
| Data + value function | `harvest/forecast.py`, `harvest/value.py`, `harvest/past_data.py`, `data/`, `scripts/` | `<NAME>` |
| Planner | `harvest/planners/` | `<NAME>` |
| Loop + results | `harvest/simulate.py`, `run.py`, `compare.py`, `replay.py`, `tests/` | `<NAME>` |
| Web app | `webapp/` | `<NAME>` |
| Farmer site | `farmer/` | `<NAME>` |

`params.yaml` is shared: add keys freely, but tell the owner before changing someone else's.

## Rules
1. Keep the inputs, outputs and array shapes in `harvest/README.md` unless the human explicitly asks. Propose a change instead.
2. Edit only the files your owner row lists, unless asked.
3. Before saying "done", run the check command and paste its output.
4. No new dependencies without asking.
5. Never run `git commit` or `git push` yourself. `git add` your changes and propose a message starting with the area (`planner: look-ahead`). The human commits. No Co-Authored-By or "Generated with Claude" lines.
6. When compacting, keep the list of files modified so far and the check command.
7. Simplest thing that works. Short functions, clear names, comments that explain why. No classes or frameworks.

## Conventions
- Every number goes in `params.yaml`, never hard-coded (numbers only the web app uses: `webapp/demo.yaml`).
- Units in names: `_gbp`, `_ha`, `_hours`, `_pct`, `_mm`. Docstrings give array shapes, e.g. `[member, field, hour]`.
- Arrays: `[member, field, hour]` for weather, `[field, hour]` for value, `[machine, hour]` for schedules.
- The value function works per forecast member and averages only at the end.
- Tests never use the internet.
