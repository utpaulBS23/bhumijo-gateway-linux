.PHONY: help install uninstall start stop restart status logs test clean

help:
	@echo "Bhumijo Gateway Service - Make Commands"
	@echo ""
	@echo "Installation:"
	@echo "  make install      - Install service (requires sudo)"
	@echo "  make uninstall    - Remove service (requires sudo)"
	@echo ""
	@echo "Service Control:"
	@echo "  make start        - Start service"
	@echo "  make stop         - Stop service"
	@echo "  make restart      - Restart service"
	@echo "  make status       - Check service status"
	@echo ""
	@echo "Monitoring:"
	@echo "  make logs         - Follow service logs"
	@echo "  make test         - Run test suite"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-build - Build Docker image"
	@echo "  make docker-up    - Start Docker container"
	@echo "  make docker-down  - Stop Docker container"
	@echo "  make docker-logs  - View Docker logs"
	@echo ""

install:
	sudo bash install.sh

uninstall:
	sudo systemctl stop bhumijo-gateway
	sudo systemctl disable bhumijo-gateway
	sudo rm -f /etc/systemd/system/bhumijo-gateway.service
	sudo rm -rf /opt/bhumijo-gateway
	sudo rm -rf /etc/bhumijo-gateway
	sudo systemctl daemon-reload
	@echo "Service uninstalled"

start:
	sudo systemctl start bhumijo-gateway
	@echo "Service started"

stop:
	sudo systemctl stop bhumijo-gateway
	@echo "Service stopped"

restart:
	sudo systemctl restart bhumijo-gateway
	@echo "Service restarted"

status:
	sudo systemctl status bhumijo-gateway

logs:
	sudo journalctl -u bhumijo-gateway -f

test:
	bash test_gateway.sh

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
	find . -type f -name "*.pyc" -delete
	@echo "Cleaned up"

docker-build:
	docker build -t bhumijo-gateway .

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

docker-logs:
	docker-compose logs -f gateway

docker-clean:
	docker-compose down -v
	docker rmi bhumijo-gateway