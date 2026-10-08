PORT ?= 8000

.PHONY: help serve stop open build clean install-programs uninstall-programs

help:
	@echo "make serve   - serve the command finder at http://localhost:$(PORT)"
	@echo "make stop    - stop whatever server is listening on port $(PORT)"
	@echo "make open    - open it in the browser (run 'make serve' in another terminal)"
	@echo "make build   - regenerate commands.json from data/menus.json + data/entries.json"
	@echo "make install-programs   - store programs/prob.rpl on the calculator (needs XSERV running)"
	@echo "make uninstall-programs - purge those programs from the calculator"
	@echo "make clean   - remove generated commands.json"

serve: commands.json
	@echo "http://localhost:$(PORT)"
	python3 -m http.server $(PORT)

stop:
	@pids=$$(lsof -ti tcp:$(PORT) -sTCP:LISTEN); \
	if [ -n "$$pids" ]; then kill $$pids && echo "stopped server on port $(PORT)"; \
	else echo "nothing listening on port $(PORT)"; fi

open:
	open http://localhost:$(PORT)

commands.json: build.py data/menus.json data/entries.json data/topics.json
	python3 build.py data

build:
	python3 build.py data

clean:
	rm -f commands.json

PROGRAMS := PBIN CBIN PPOI CPOI CEXP BAYES EX VX PGEO PNB

install-programs:
	@./hp --wait eval "$$(grep '^«' programs/prob.rpl | tr '\n' ' ')" >/dev/null && echo "installed: $(PROGRAMS)"

uninstall-programs:
	@./hp --wait eval "$(foreach p,$(PROGRAMS),IFERR '$(p)' PURGE THEN END )" >/dev/null && echo "purged: $(PROGRAMS)"
