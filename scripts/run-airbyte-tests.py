#!/usr/bin/env python3
"""
Airbyte Comprehensive Test Runner

This script runs all Airbyte-related tests including:
- Unit tests for configuration and client code
- Integration tests for running deployment
- Health checks and diagnostics
- Performance benchmarks
"""

import sys
import os
import unittest
import argparse
import subprocess
import time
import json
from pathlib import Path
from typing import List, Dict, Any, Optional


class TestRunner:
    """Comprehensive test runner for Airbyte deployment."""
    
    def __init__(self):
        self.results = {
            'unit_tests': {},
            'integration_tests': {},
            'health_checks': {},
            'performance_tests': {}
        }
        self.start_time = time.time()
    
    def run_unit_tests(self) -> bool:
        """Run unit tests for Airbyte components."""
        print("Running Airbyte Unit Tests...")
        print("-" * 40)
        
        try:
            # Import and run unit tests
            from tests.test_airbyte_deployment import create_test_suite
            
            runner = unittest.TextTestRunner(verbosity=2)
            suite = create_test_suite()
            result = runner.run(suite)
            
            self.results['unit_tests'] = {
                'run': result.testsRun,
                'failures': len(result.failures),
                'errors': len(result.errors),
                'success': result.wasSuccessful()
            }
            
            return result.wasSuccessful()
            
        except ImportError as e:
            print(f"❌ Could not import unit tests: {e}")
            self.results['unit_tests'] = {
                'run': 0,
                'failures': 0,
                'errors': 1,
                'success': False,
                'error': str(e)
            }
            return False
        except Exception as e:
            print(f"❌ Unit test execution failed: {e}")
            self.results['unit_tests'] = {
                'run': 0,
                'failures': 0,
                'errors': 1,
                'success': False,
                'error': str(e)
            }
            return False
    
    def run_integration_tests(self, quick_only: bool = False) -> bool:
        """Run integration tests (require running services)."""
        print("\nRunning Airbyte Integration Tests...")
        print("-" * 40)
        
        try:
            from tests.test_airbyte_integration import create_integration_test_suite
            
            runner = unittest.TextTestRunner(verbosity=2)
            suite = create_integration_test_suite()
            
            # Filter tests if quick mode
            if quick_only:
                # Remove slow tests (those without 'quick' in name)
                filtered_suite = unittest.TestSuite()
                for test_group in suite:
                    if hasattr(test_group, '_tests'):
                        for test in test_group._tests:
                            if 'test_01' in str(test) or 'quick' in str(test).lower():
                                filtered_suite.addTest(test)
                suite = filtered_suite
            
            result = runner.run(suite)
            
            self.results['integration_tests'] = {
                'run': result.testsRun,
                'failures': len(result.failures),
                'errors': len(result.errors),
                'skipped': len(result.skipped),
                'success': result.wasSuccessful()
            }
            
            return result.wasSuccessful()
            
        except ImportError as e:
            print(f"❌ Could not import integration tests: {e}")
            self.results['integration_tests'] = {
                'run': 0,
                'failures': 0,
                'errors': 1,
                'success': False,
                'error': str(e)
            }
            return False
        except Exception as e:
            print(f"❌ Integration test execution failed: {e}")
            self.results['integration_tests'] = {
                'run': 0,
                'failures': 0,
                'errors': 1,
                'success': False,
                'error': str(e)
            }
            return False
    
    def run_health_checks(self) -> Dict[str, Any]:
        """Run health checks using the monitoring script."""
        print("\nRunning Health Checks...")
        print("-" * 40)
        
        try:
            # Run the monitoring script once
            result = subprocess.run([
                sys.executable, 'scripts/airbyte-monitor.py', '--once', '--output', 'health-check-result.json'
            ], capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0:
                # Read the generated report
                if os.path.exists('health-check-result.json'):
                    with open('health-check-result.json', 'r') as f:
                        report = json.load(f)
                    
                    health_status = report.get('overall_status', 'unknown')
                    services_checked = len(report.get('services', []))
                    
                    print(f"✅ Health Check Status: {health_status}")
                    print(f"✅ Services Checked: {services_checked}")
                    
                    self.results['health_checks'] = {
                        'status': health_status,
                        'services_checked': services_checked,
                        'alerts': len(report.get('alerts', [])),
                        'recommendations': len(report.get('recommendations', [])),
                        'success': health_status in ['healthy', 'degraded']
                    }
                    
                    return self.results['health_checks']
                else:
                    print("❌ Health check report not generated")
                    self.results['health_checks'] = {
                        'status': 'unknown',
                        'success': False,
                        'error': 'Report file not found'
                    }
                    return self.results['health_checks']
            else:
                print(f"❌ Health check failed: {result.stderr}")
                self.results['health_checks'] = {
                    'status': 'unknown',
                    'success': False,
                    'error': result.stderr[:200]  # Truncate long errors
                }
                return self.results['health_checks']
                
        except subprocess.TimeoutExpired:
            print("❌ Health check timed out")
            self.results['health_checks'] = {
                'status': 'timeout',
                'success': False,
                'error': 'Health check timed out after 120 seconds'
            }
            return self.results['health_checks']
        except Exception as e:
            print(f"❌ Health check execution failed: {e}")
            self.results['health_checks'] = {
                'status': 'error',
                'success': False,
                'error': str(e)
            }
            return self.results['health_checks']
    
    def run_performance_tests(self) -> bool:
        """Run performance benchmark tests."""
        print("\nRunning Performance Tests...")
        print("-" * 40)
        
        # Placeholder for performance tests
        # In a real implementation, this would test:
        # - Startup time measurements
        # - API response times
        # - Memory usage patterns
        # - Concurrent operation performance
        
        performance_results = {
            'startup_time': self._measure_startup_time(),
            'api_response_times': self._measure_api_performance(),
            'memory_usage': self._measure_memory_usage()
        }
        
        self.results['performance_tests'] = performance_results
        
        # For now, always return success
        print("✅ Performance tests completed (placeholder)")
        return True
    
    def _measure_startup_time(self) -> Dict[str, Any]:
        """Measure service startup times."""
        # This would measure actual startup times
        return {
            'measurement': 'placeholder',
            'average_startup_time': 0,
            'max_startup_time': 0
        }
    
    def _measure_api_performance(self) -> Dict[str, Any]:
        """Measure API endpoint performance."""
        # This would test API response times
        return {
            'measurement': 'placeholder',
            'avg_response_time_ms': 0,
            '95th_percentile_ms': 0
        }
    
    def _measure_memory_usage(self) -> Dict[str, Any]:
        """Measure memory usage patterns."""
        # This would monitor memory consumption
        return {
            'measurement': 'placeholder',
            'peak_memory_mb': 0,
            'average_memory_mb': 0
        }
    
    def generate_report(self, output_file: Optional[str] = None) -> str:
        """Generate comprehensive test report."""
        total_duration = time.time() - self.start_time
        
        report = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'duration_seconds': round(total_duration, 2),
            'results': self.results,
            'summary': self._generate_summary()
        }
        
        # Print console summary
        print("\n" + "=" * 60)
        print("AIRBYTE COMPREHENSIVE TEST REPORT")
        print("=" * 60)
        print(f"Execution Time: {report['duration_seconds']} seconds")
        print(f"Timestamp: {report['timestamp']}")
        print()
        
        for test_type, results in self.results.items():
            if results:
                status_icon = "✅" if results.get('success', False) else "❌"
                print(f"{status_icon} {test_type.replace('_', ' ').title()}")
                if 'run' in results:
                    print(f"    Tests Run: {results['run']}")
                    print(f"    Failures: {results['failures']}")
                    print(f"    Errors: {results['errors']}")
                elif 'status' in results:
                    print(f"    Status: {results['status']}")
                print()
        
        print("SUMMARY:")
        print(f"  Overall Success: {'✅ PASS' if report['summary']['overall_success'] else '❌ FAIL'}")
        print(f"  Total Tests: {report['summary']['total_tests']}")
        print(f"  Passed: {report['summary']['passed']}")
        print(f"  Failed: {report['summary']['failed']}")
        print(f"  Success Rate: {report['summary']['success_rate']:.1f}%")
        
        # Save to file if requested
        if output_file:
            with open(output_file, 'w') as f:
                json.dump(report, f, indent=2, default=str)
            print(f"\nDetailed report saved to: {output_file}")
        
        return json.dumps(report, indent=2, default=str)
    
    def _generate_summary(self) -> Dict[str, Any]:
        """Generate summary statistics."""
        total_tests = 0
        passed_tests = 0
        failed_tests = 0
        
        for test_type, results in self.results.items():
            if 'run' in results:
                total_tests += results['run']
                if results['success']:
                    passed_tests += results['run'] - results['failures'] - results['errors']
                    failed_tests += results['failures'] + results['errors']
        
        success_rate = (passed_tests / total_tests * 100) if total_tests > 0 else 0
        
        return {
            'overall_success': all(r.get('success', False) for r in self.results.values() if r),
            'total_tests': total_tests,
            'passed': passed_tests,
            'failed': failed_tests,
            'success_rate': success_rate
        }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Airbyte Comprehensive Test Runner')
    parser.add_argument('--unit-only', action='store_true',
                       help='Run only unit tests')
    parser.add_argument('--integration-only', action='store_true',
                       help='Run only integration tests')
    parser.add_argument('--quick', action='store_true',
                       help='Run quick tests only (skip slow integration tests)')
    parser.add_argument('--health-only', action='store_true',
                       help='Run only health checks')
    parser.add_argument('--performance-only', action='store_true',
                       help='Run only performance tests')
    parser.add_argument('--all', action='store_true',
                       help='Run all tests (default)')
    parser.add_argument('--output', '-o',
                       help='Output file for detailed JSON report')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')
    
    args = parser.parse_args()
    
    # Determine which tests to run
    run_all = args.all or not any([args.unit_only, args.integration_only, 
                                  args.health_only, args.performance_only])
    
    # Create test runner
    runner = TestRunner()
    
    success = True
    
    # Run selected tests
    if run_all or args.unit_only:
        success &= runner.run_unit_tests()
    
    if run_all or args.integration_only:
        success &= runner.run_integration_tests(quick_only=args.quick)
    
    if run_all or args.health_only:
        health_result = runner.run_health_checks()
        success &= health_result.get('success', False)
    
    if run_all or args.performance_only:
        success &= runner.run_performance_tests()
    
    # Generate report
    report = runner.generate_report(args.output)
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
