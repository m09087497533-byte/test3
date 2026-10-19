import socket
import ssl
import unittest
import urllib.error
from unittest.mock import patch
from studio.network import tls_context,connection_error,diagnostics,open_with_retry

class NetworkTest(unittest.TestCase):
    def test_short_http_429_is_retried_then_succeeds(self):
        error=urllib.error.HTTPError('https://example.test/',429,'limit',{'Retry-After':'1'},None)
        with patch('studio.network.time.sleep') as pause:
            opener=unittest.mock.Mock()
            opener.open.side_effect=[error,error,'response']
            self.assertEqual(open_with_retry(opener,'request',5),'response')
        self.assertEqual(opener.open.call_count,3)
        self.assertEqual(pause.call_count,2)

    def test_certificate_verification_stays_enabled(self):
        context=tls_context()
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(context.cert_store_stats()['x509_ca'],0)

    def test_specific_safe_errors(self):
        cases=[(urllib.error.URLError(ssl.SSLCertVerificationError(1,'certificate verify failed')),'SSL'),
               (urllib.error.URLError(OSError('Tunnel connection failed: 403 Forbidden')),'прокси'),
               (urllib.error.URLError(socket.gaierror(-2,'Name not known')),'DNS'),
               (urllib.error.URLError(TimeoutError()),'вовремя')]
        for error,expected in cases:self.assertIn(expected,connection_error(error,'Pixabay'))
        error=urllib.error.HTTPError('https://pixabay.com/api/?key=SECRET',401,'bad key SECRET',{},None)
        result=connection_error(error,'Pixabay')
        self.assertIn('API-ключ',result);self.assertNotIn('SECRET',result)

    def test_diagnostic_http_auth_rejection_means_connection_worked(self):
        error=urllib.error.HTTPError('https://example.test/',401,'auth',{},None)
        with patch('studio.network.urllib.request.build_opener') as factory:
            factory.return_value.open.side_effect=error
            result=diagnostics()
        self.assertEqual(len(result),3)
        self.assertTrue(all(x['ok'] for x in result))
        self.assertTrue(all('без ключа' in x['message'] for x in result))

    def test_diagnostic_cert_failure_remains_failure(self):
        with patch('studio.network.urllib.request.build_opener') as factory:
            factory.return_value.open.side_effect=urllib.error.URLError(ssl.SSLCertVerificationError())
            result=diagnostics()
        self.assertTrue(all(not x['ok'] for x in result))
        self.assertTrue(all('SSL' in x['message'] for x in result))
