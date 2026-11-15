PYTHON ?= python
INPUT ?= fixtures/site
CONTRACT ?= fixtures/contracts/site.yaml
OUT ?= out
NOW ?= 2026-01-01T00:00:00Z
DB ?= $(OUT)/ledger.sqlite3
EXPECTED ?= fixtures/expected/report_summary.json

.PHONY: test demo serve clean

test:
	$(PYTHON) -m pytest -q

demo:
	$(PYTHON) -m microtag_harvest run --input $(INPUT) --contract $(CONTRACT) --out $(OUT) --now $(NOW)
	$(PYTHON) -m microtag_harvest golden --report $(OUT)/report.json --expected $(EXPECTED)

serve:
	@test -f "$(DB)" || $(MAKE) demo
	$(PYTHON) -m microtag_harvest serve --db $(DB)

clean:
	rm -rf "$(OUT)"
