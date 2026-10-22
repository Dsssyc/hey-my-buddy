"""A persistent login remains writable after reopening the same console."""
from console.test_console import ConsoleTestCase


class ConsoleReopenRegressionTests(ConsoleTestCase):
    def test_same_console_reopen_refreshes_persistent_session_csrf(self):
        board = self.board()
        board.console._clock = lambda: 1000.0
        board.console.read_cache._clock = lambda: 1000.0
        _, browser = self.open_console(board)
        first = browser.bootstrap()
        console = board.console
        board.console.close()
        board.console.port = browser.port
        board.console.start(issue_ticket=False)
        reopened = browser.bootstrap()
        self.assertIs(board.console, console)
        self.assertEqual(first['consoleSession']['id'], reopened['consoleSession']['id'])
        live = board.console._sessions.sessions[reopened['consoleSession']['id']]
        self.assertNotEqual(first['csrfToken'], live.csrf)
        status, _, body = browser.command('selection_list', {}, csrf=reopened['csrfToken'])
        self.assertEqual(status, 200, body)
        self.assertNotEqual(first['csrfToken'], reopened['csrfToken'])
