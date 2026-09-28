# Host deploys. The logic lives in ops/deploy-host.sh; these targets only name its
# steps. Workflow changes need no deploy: automations read .fabro/workflows from
# `main` at fire time. Run a deploy after a push that touches ops/ or
# .fabro/workflows/backlog/scripts/.
#
# Variables, on the command line or in the environment:
#   HOST=andrew@10.10.0.32  FORCE=1  ALLOW_DIRTY=1  SKIP_IMAGES=1  CONFIRM=1
# See the header of ops/deploy-host.sh for what each one does.

DEPLOY := ./ops/deploy-host.sh

.DEFAULT_GOAL := help
.PHONY: help deploy deploy-notify deploy-scripts deploy-compose deploy-scheduler \
	deploy-images provision verify-host compose-up

help:
	@echo "make deploy            every step below except compose-up, then verify-host"
	@echo "make deploy-notify     discord-notify.sh into the fabro container"
	@echo "make deploy-scripts    operator scripts into ~/bin"
	@echo "make deploy-compose    copy docker-compose.yaml (does not apply it to fabro)"
	@echo "make deploy-scheduler  rsync + rebuild the scheduler if its tree changed"
	@echo "make deploy-images     rsync + rebuild the profile images if their inputs changed"
	@echo "make provision         create any missing automation or server variable"
	@echo "make verify-host       diff every host copy against this checkout"
	@echo "make compose-up CONFIRM=1   apply the compose file to fabro (fails in-flight runs)"

deploy:
	$(DEPLOY) all

deploy-notify:
	$(DEPLOY) notify

deploy-scripts:
	$(DEPLOY) scripts

deploy-compose:
	$(DEPLOY) compose

deploy-scheduler:
	$(DEPLOY) scheduler

deploy-images:
	$(DEPLOY) images

provision:
	$(DEPLOY) provision

verify-host:
	$(DEPLOY) verify

compose-up:
	$(DEPLOY) compose-up
