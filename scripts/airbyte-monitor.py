#!/usr/bin/env python3
"""
Airbyte Monitoring and Health Check Script

This script provides comprehensive monitoring for the Airbyte deployment,
including health checks, performance metrics, alerting, and automated
recovery mechanisms.
"""

import sys
import time
import json
import logging
import argparse
import subprocess
import requests
import smtplib
import socket
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading


@dataclass
class ServiceStatus:
    """Represents the status of a service."""
    name: str
    status: str  # 'healthy', 'unhealthy', 'degraded', 'unknown'
    response_time: Optional[float] = None
    last_checked: Optional[datetime] = None
    error_message: Optional[str] = None
    metrics: Optional[Dict[str, Any]] = None


@dataclass  
class HealthReport:
    """Complete health report for the Airbyte deployment."""
    timestamp: datetime
    overall_status: str  # 'healthy', 'degraded', 'critical'
    services: List[ServiceStatus]
    system_metrics: Dict[str, Any]
    alerts: List[str]
    recommendations: List[str]


class AirbyteMonitor:
    """Main monitoring class for Airbyte deployment."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = self._setup_logging()
        self.services = self._initialize_services()
        self.alert_history = []
        self.last_report = None
        
    def _setup_logging(self) -> logging.Logger:
        """Set up logging configuration."""
        logger = logging.getLogger('airbyte_monitor')
        logger.setLevel(logging.INFO)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # File handler for detailed logs
        if self.config.get('log_file'):
            file_handler = logging.FileHandler(self.config['log_file'])
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            
        return logger
    
    def _initialize_services(self) -> List[Dict[str, Any]]:
        """Initialize service configurations."""
        return [
            {
                'name': 'airbyte-db',
                'type': 'database',
                'host': 'localhost',
                'port': 5432,
                'check_type': 'tcp',
                'expected_response': None
            },
            {
                'name': 'airbyte-temporal',
                'type': 'service',
                'host': 'localhost', 
                'port': 7233,
                'check_type': 'tcp',
                'expected_response': None
            },
            {
                'name': 'airbyte-server',
                'type': 'api',
                'host': 'localhost',
                'port': 11021,
                'check_type': 'http',
                'endpoint': '/api/v1/health',
                'expected_status': 200
            },
            {
                'name': 'airbyte-webapp',
                'type': 'web',
                'host': 'localhost',
                'port': 11020,
                'check_type': 'http',
                'endpoint': '/',
                'expected_status': 200
            }
        ]
    
    def check_service_health(self, service: Dict[str, Any]) -> ServiceStatus:
        """Check the health of a single service."""
        start_time = time.time()
        
        try:
            if service['check_type'] == 'tcp':
                status, error = self._check_tcp_port(
                    service['host'], service['port']
                )
            elif service['check_type'] == 'http':
                status, error = self._check_http_endpoint(
                    service['host'], service['port'], 
                    service.get('endpoint', '/'),
                    service.get('expected_status', 200)
                )
            else:
                status, error = 'unknown', f"Unknown check type: {service['check_type']}"
                
            response_time = (time.time() - start_time) * 1000  # ms
            
            return ServiceStatus(
                name=service['name'],
                status=status,
                response_time=response_time,
                last_checked=datetime.now(),
                error_message=error
            )
            
        except Exception as e:
            self.logger.error(f"Error checking {service['name']}: {e}")
            return ServiceStatus(
                name=service['name'],
                status='unknown',
                last_checked=datetime.now(),
                error_message=str(e)
            )
    
    def _check_tcp_port(self, host: str, port: int) -> Tuple[str, Optional[str]]:
        """Check if a TCP port is open and responsive."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.config.get('tcp_timeout', 5))
                result = sock.connect_ex((host, port))
                
                if result == 0:
                    return 'healthy', None
                else:
                    return 'unhealthy', f"Port {port} closed or unreachable"
                    
        except Exception as e:
            return 'unhealthy', str(e)
    
    def _check_http_endpoint(self, host: str, port: int, endpoint: str, 
                           expected_status: int) -> Tuple[str, Optional[str]]:
        """Check HTTP endpoint health."""
        try:
            url = f"http://{host}:{port}{endpoint}"
            response = requests.get(
                url, 
                timeout=self.config.get('http_timeout', 10),
                allow_redirects=False
            )
            
            if response.status_code == expected_status:
                return 'healthy', None
            elif 200 <= response.status_code < 300:
                return 'degraded', f"Status {response.status_code}, expected {expected_status}"
            else:
                return 'unhealthy', f"Status {response.status_code}, expected {expected_status}"
                
        except requests.exceptions.Timeout:
            return 'unhealthy', "Request timeout"
        except requests.exceptions.ConnectionError:
            return 'unhealthy', "Connection failed"
        except Exception as e:
            return 'unhealthy', str(e)
    
    def collect_system_metrics(self) -> Dict[str, Any]:
        """Collect system-level metrics."""
        metrics = {}
        
        try:
            # Docker metrics
            result = subprocess.run(
                ['docker', 'stats', '--no-stream', '--format', 
                 '{"name":"{{.Name}}","cpu":"{{.CPUPerc}}","mem":"{{.MemUsage}}"}'],
                capture_output=True, text=True, timeout=10
            )
            
            if result.returncode == 0:
                docker_stats = []
                for line in result.stdout.strip().split('\n'):
                    if line and 'rag-airbyte' in line:
                        try:
                            stat = json.loads(line)
                            docker_stats.append(stat)
                        except json.JSONDecodeError:
                            continue
                metrics['docker_stats'] = docker_stats
                
        except Exception as e:
            self.logger.warning(f"Could not collect Docker metrics: {e}")
        
        # Disk usage
        try:
            result = subprocess.run(
                ['df', '-h', 'data/airbyte'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                metrics['disk_usage'] = result.stdout.strip()
        except Exception:
            pass
            
        return metrics
    
    def generate_health_report(self) -> HealthReport:
        """Generate a complete health report."""
        service_statuses = []
        alerts = []
        recommendations = []
        
        # Check all services
        for service in self.services:
            status = self.check_service_health(service)
            service_statuses.append(status)
            
            # Generate alerts for unhealthy services
            if status.status == 'unhealthy':
                alerts.append(f"Service {status.name} is unhealthy: {status.error_message}")
                recommendations.append(f"Restart {status.name} service")
            elif status.status == 'degraded':
                alerts.append(f"Service {status.name} is degraded")
        
        # Overall status determination
        unhealthy_count = sum(1 for s in service_statuses if s.status == 'unhealthy')
        degraded_count = sum(1 for s in service_statuses if s.status == 'degraded')
        
        if unhealthy_count > 0:
            overall_status = 'critical'
        elif degraded_count > 0:
            overall_status = 'degraded'
        else:
            overall_status = 'healthy'
        
        # Collect system metrics
        system_metrics = self.collect_system_metrics()
        
        report = HealthReport(
            timestamp=datetime.now(),
            overall_status=overall_status,
            services=service_statuses,
            system_metrics=system_metrics,
            alerts=alerts,
            recommendations=recommendations
        )
        
        self.last_report = report
        return report
    
    def send_alert(self, message: str, severity: str = 'warning'):
        """Send alert notifications."""
        self.logger.warning(f"[{severity.upper()}] {message}")
        self.alert_history.append({
            'timestamp': datetime.now(),
            'severity': severity,
            'message': message
        })
        
        # Send email alert if configured
        if self.config.get('email_alerts', {}).get('enabled'):
            self._send_email_alert(message, severity)
    
    def _send_email_alert(self, message: str, severity: str):
        """Send email notification."""
        email_config = self.config['email_alerts']
        
        try:
            msg = MIMEMultipart()
            msg['From'] = email_config['from']
            msg['To'] = ', '.join(email_config['to'])
            msg['Subject'] = f"Airbyte Alert - {severity.upper()}"
            
            body = f"""
Airbyte Monitoring Alert

Time: {datetime.now().isoformat()}
Severity: {severity.upper()}
Message: {message}

This is an automated alert from the Airbyte monitoring system.
            """
            msg.attach(MIMEText(body, 'plain'))
            
            server = smtplib.SMTP(email_config['smtp_server'], email_config['smtp_port'])
            if email_config.get('use_tls', True):
                server.starttls()
            if email_config.get('username'):
                server.login(email_config['username'], email_config['password'])
            
            server.send_message(msg)
            server.quit()
            
            self.logger.info("Email alert sent successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to send email alert: {e}")
    
    def auto_recovery(self, service_name: str) -> bool:
        """Attempt automatic recovery of a service."""
        self.logger.info(f"Attempting auto-recovery for {service_name}")
        
        try:
            # Restart the specific service
            result = subprocess.run([
                'docker-compose', '-f', 'docker-compose.yml', 
                '-f', 'docker-compose.airbyte.yml', 'restart', service_name
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                self.logger.info(f"Successfully restarted {service_name}")
                return True
            else:
                self.logger.error(f"Failed to restart {service_name}: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Auto-recovery failed for {service_name}: {e}")
            return False
    
    def run_continuous_monitoring(self, interval: int = 60):
        """Run continuous monitoring loop."""
        self.logger.info(f"Starting continuous monitoring (interval: {interval}s)")
        
        while True:
            try:
                report = self.generate_health_report()
                
                # Log summary
                self.logger.info(
                    f"Health Check - Status: {report.overall_status} "
                    f"({len(report.services)} services checked)"
                )
                
                # Send alerts for critical issues
                if report.overall_status == 'critical':
                    alert_msg = f"Airbyte deployment is critical. Issues: {', '.join(report.alerts)}"
                    self.send_alert(alert_msg, 'critical')
                    
                    # Attempt auto-recovery for critical services
                    for service in report.services:
                        if service.status == 'unhealthy':
                            if self.auto_recovery(service.name):
                                self.send_alert(
                                    f"Auto-recovery succeeded for {service.name}", 
                                    'info'
                                )
                            else:
                                self.send_alert(
                                    f"Auto-recovery failed for {service.name}", 
                                    'critical'
                                )
                
                # Send periodic status reports
                if self.config.get('periodic_reports', {}).get('enabled'):
                    self._send_periodic_report(report)
                
                time.sleep(interval)
                
            except KeyboardInterrupt:
                self.logger.info("Monitoring stopped by user")
                break
            except Exception as e:
                self.logger.error(f"Error in monitoring loop: {e}")
                time.sleep(interval)
    
    def _send_periodic_report(self, report: HealthReport):
        """Send periodic status reports."""
        # Implementation would send daily/weekly summaries
        pass


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Airbyte Monitoring Tool')
    parser.add_argument('--config', '-c', default='monitoring.json',
                       help='Configuration file path')
    parser.add_argument('--once', action='store_true',
                       help='Run health check once and exit')
    parser.add_argument('--continuous', action='store_true',
                       help='Run continuous monitoring')
    parser.add_argument('--interval', type=int, default=60,
                       help='Monitoring interval in seconds (default: 60)')
    parser.add_argument('--output', '-o', 
                       help='Output file for JSON report')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')
    
    args = parser.parse_args()
    
    # Load configuration
    try:
        with open(args.config, 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        # Use default configuration
        config = {
            'tcp_timeout': 5,
            'http_timeout': 10,
            'log_file': 'airbyte_monitor.log',
            'email_alerts': {
                'enabled': False
            },
            'periodic_reports': {
                'enabled': False
            }
        }
    
    if args.verbose:
        config['log_level'] = 'DEBUG'
    
    # Create monitor
    monitor = AirbyteMonitor(config)
    
    if args.once:
        # Run single health check
        report = monitor.generate_health_report()
        
        # Output results
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(asdict(report), f, indent=2, default=str)
        
        # Print summary
        print(f"Overall Status: {report.overall_status}")
        print(f"Services Checked: {len(report.services)}")
        print("\nService Statuses:")
        for service in report.services:
            status_icon = {
                'healthy': '✓',
                'degraded': '⚠',
                'unhealthy': '✗',
                'unknown': '?'
            }.get(service.status, '?')
            print(f"  {status_icon} {service.name}: {service.status}")
            if service.error_message:
                print(f"    Error: {service.error_message}")
        
        if report.alerts:
            print("\nAlerts:")
            for alert in report.alerts:
                print(f"  ⚠ {alert}")
        
        if report.recommendations:
            print("\nRecommendations:")
            for rec in report.recommendations:
                print(f"  • {rec}")
        
        # Exit with appropriate code
        sys.exit(0 if report.overall_status == 'healthy' else 1)
        
    elif args.continuous:
        # Run continuous monitoring
        monitor.run_continuous_monitoring(args.interval)
        
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
