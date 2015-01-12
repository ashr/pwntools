"""
Logging module for printing status during an exploit, and internally
within ``pwntools``.

Exploit Developers
------------------
By using the standard ``from pwn import *``, an object named ``log`` will
be inserted into the global namespace.  You can use this to print out
status messages during exploitation.

For example,::

    log.info('Hello, world!')

prints::

    [*] Hello, world!

Additionally, there are some nifty mechanisms for performing status updates
on a running job (e.g. when brute-forcing).::

    p = log.progress('Working')
    p.status('Reticulating splines')
    time.sleep(1)
    p.success('Got a shell!')


The verbosity of logging can be most easily controlled by setting
``log_level`` on the global ``context`` object.::

    log.info("No you see me")
    context.log_level = 'error'
    log.info("Now you don't")

The purpose of this attribute is to control what gets printed to the screen,
not what gets emitted. This means that you can put all logging events into
a log file, while only wanting to see a small subset of them on your screen.

Pwnlib Developers
-----------------
A module-specific logger can be imported into the module via::

    from .log import getLogger
    log = getLogger(__name__)

This provides an easy way to filter logging programmatically
or via a configuration file for debugging.

When using ``progress``, you should use the ``with``
keyword to manage scoping, to ensure the spinner stops if an
exception is thrown.

Technical details
-----------------
Familiarity with the :mod:`logging` module is assumed.

A pwnlib root logger named 'pwnlib' is created and a custom handler and
formatter is installed for it.  The handler determines its logging level from
:data:`context.log_level`.

Ideally :data:`context.log_level` should only affect which records will be
emitted by the handler such that e.g. logging to a file will not be changed by
it. But for performance reasons it is not feasible log everything in the normal
case. In particular there are tight loops inside :mod:`pwnlib.tubes.tube`, which
we would like to be able to debug, but if we are not debugging them, they should
not spit out messages (even to a log file). For this reason there are a few places
inside pwnlib, that will not even emit a record without :data:`context.log_level`
being set to `logging.DEBUG` or below.

Log records created by ``Progress`` and ``Logger`` objects will set
``'pwnlib_msgtype'`` on the ``extra`` field to signal which kind of message was
generated.  This information is used by the formatter to prepend a symbol to the
message, e.g. ``'[+] '`` in ``'[+] got a shell!'``

This field is ignored when using the ``logging`` module's standard formatters.

All status updates (which are not dropped due to throttling) on progress loggers
result in a log record being created.  The ``extra`` field then carries a
reference to the ``Progress`` logger as ``'pwnlib_progress'``.

If the custom handler determines that :data:`term.term_mode` is enabled, log
records that have a ``'pwnlib_progess'`` in their ``extra`` field will not
result in a message being emitted but rather an animated progress line (with a
spinner!) being created.  Note that other handlers will still see a meaningful
log record.

The custom handler will only handle log records whith a level of at least
:data:`context.log_level`.  Thus if e.g. the level for the
``'pwnlib.tubes.ssh'`` is set to ``'DEBUG'`` no additional output will show up
unless :data:`context.log_level` is also set to ``'DEBUG'``.  Other handlers
will however see the extra log records generated by the ``'pwnlib.tubes.ssh'``
logger.
"""

__all__ = [
    'getLogger', 'install_default_handler', 'rootlogger'
]

import logging, re, threading, sys, random, time
from .context   import context, Thread
from .exception import PwnlibException
from .term      import spinners, text
from .          import term

# list of prefixes to use for the different message types.  note that the `text`
# module won't add any escape codes if `sys.stderr.isatty()` is `False`
_msgtype_prefixes = {
    'status'       : (text.magenta, 'x'),
    'success'      : (text.bold_green, '+'),
    'failure'      : (text.bold_red, '-'),
    'debug'        : (text.bold_red, 'DEBUG'),
    'info'         : (text.bold_blue, '*'),
    'warning'      : (text.bold_yellow, '!'),
    'error'        : (text.on_red, 'ERROR'),
    'exception'    : (text.on_red, 'ERROR'),
    'critical'     : (text.on_red, 'CRITICAL'),
    'info_once'    : (text.bold_blue, '*'),
    'warning_once' : (text.bold_yellow, '!'),
    }

# the text decoration to use for spinners.  the spinners themselves can be found
# in the `pwnlib.term.spinners` module
_spinner_style = text.bold_blue

class Progress(object):
    """
    Progress logger used to generate log records associated with some running
    job.  Instances can be used as context managers which will automatically
    declare the running job a success upon exit or a failure upon a thrown
    exception.  After :meth:`success` or :meth:`failure` is called the status
    can no longer be updated.

    This class is intended for internal use.  Progress loggers should be created
    using :meth:`Logger.progress`.
    """
    def __init__(self, logger, msg, status, level, args, kwargs):
        global _progressid
        self._logger = logger
        self._msg = msg
        self._status = status
        self._level = level
        self._stopped = False
        self.last_status = 0
        self._log(status, args, kwargs, 'status')
        # it is a common use case to create a logger and then immediately update
        # its status line, so we reset `last_status` to accomodate this pattern
        self.last_status = 0

    def _log(self, status, args, kwargs, msgtype):
        # this progress logger is stopped, so don't generate any more records
        if self._stopped:
            return
        msg = self._msg
        if msg and status:
            msg += ': '
        msg += status
        self._logger._log(self._level, msg, args, kwargs, msgtype, self)

    def status(self, status, *args, **kwargs):
        """status(status, *args, **kwargs)

        Logs a status update for the running job.

        If the progress logger is animated the status line will be updated in
        place.

        Status updates are throttled at one update per 100ms.
        """
        now = time.time()
        if (now - self.last_status) > 0.1:
            self.last_status = now
            self._log(status, args, kwargs, 'status')

    def success(self, status = 'Done', *args, **kwargs):
        """success(status = 'Done', *args, **kwargs)

        Logs that the running job succeeded.  No further status updates are
        allowed.

        If the Logger is animated, the animation is stopped.
        """
        self._log(status, args, kwargs, 'success')
        self._stopped = True

    def failure(self, status = 'Failed', *args, **kwargs):
        """failure(message)

        Logs that the running job failed.  No further status updates are
        allowed.

        If the Logger is animated, the animation is stopped.
        """
        self._log(status, args, kwargs, 'failure')
        self._stopped = True

    def __enter__(self):
        return self

    def __exit__(self, exc_typ, exc_val, exc_tb):
        # if the progress logger is already stopped these are no-ops
        if exc_typ is None:
            self.success()
        else:
            self.failure()

class Logger(object):
    """
    A class akin to the :class:`logging.LoggerAdapter` class.  All public
    methods defined on :class:`logging.Logger` instances are defined on this
    class.

    Also adds some ``pwnlib`` flavor:

    * :meth:`progress` (alias :meth:`waitfor`)
    * :meth:`success`
    * :meth:`failure`
    * :meth:`indented`
    * :meth:`info_once`
    * :meth:`warning_once` (alias :meth:`warn_once`)

    Adds ``pwnlib``-specific information for coloring, indentation and progress
    logging via log records ``extra`` field.

    Loggers instantiated with :func:`getLogger` will be of this class.
    """
    _one_time_infos    = set()
    _one_time_warnings = set()

    def __init__(self, logger):
        self._logger = logger

    def _log(self, level, msg, args, kwargs, msgtype, progress = None):
        extra = kwargs.get('extra', {})
        extra.setdefault('pwnlib_msgtype', msgtype)
        extra.setdefault('pwnlib_progress', progress)
        kwargs['extra'] = extra
        self._logger.log(level, msg, *args, **kwargs)

    def progress(self, message, status = '', *args, **kwargs):
        """progress(message, status = '', *args, level = logging.INFO, **kwargs) -> Progress

        Creates a new progress logger which creates log records with log level
        `level`.

        Progress status can be updated using :meth:`Progress.status` and stopped
        using :meth:`Progress.success` or :meth:`Progress.failure`.

        If `term.term_mode` is enabled the progress logger will be animated.

        The progress manager also functions as a context manager.  Using context
        managers ensures that animations stop even if an exception is raised.

        .. code-block:: python

           with log.progress('Trying something...') as p:
               for i in range(10):
                   p.status("At %i" % i)
                   time.sleep(0.5)
               x = 1/0
        """
        level = kwargs.pop('level', logging.INFO)
        return Progress(self, message, status, level, args, kwargs)

    def waitfor(self, *args, **kwargs):
        """Alias for :meth:`progress`."""
        return self.progress(*args, **kwargs)

    def indented(self, message, *args, **kwargs):
        """indented(message, *args, level = logging.INFO, **kwargs)

        Log a message but don't put a line prefix on it.

        Arguments:
            level(int): Alternate log level at which to set the indented
                        message.  Defaults to :const:`logging.INFO`.
        """
        level = kwargs.pop('level', logging.INFO)
        self._log(level, message, args, kwargs, 'indented')

    def success(self, message, *args, **kwargs):
        """success(message, *args, **kwargs)

        Logs a success message.
        """
        self._log(logging.INFO, message, args, kwargs, 'success')

    def failure(self, message, *args, **kwargs):
        """failure(message, *args, **kwargs)

        Logs a failure message.
        """
        self._log(logging.INFO, message, args, kwargs, 'failure')

    def info_once(self, message, *args, **kwargs):
        """info_once(message, *args, **kwargs)

        Logs an info message.  The same message is never printed again.
        """
        m = message % args
        if m not in self._one_time_infos:
            self._one_time_infos.add(m)
            self._log(logging.INFO, message, args, kwargs, 'info_once')

    def warning_once(self, message, *args, **kwargs):
        """warning_once(message, *args, **kwargs)

        Logs a warning message.  The same message is never printed again.
        """
        m = message % args
        if m not in self._one_time_warnings:
            self._one_time_warnings.add(m)
            self._log(logging.WARNING, message, args, kwargs, 'warning_once')

    def warn_once(self, *args, **kwargs):
        """Alias for :meth:`warning_once`."""
        return self.warning(*args, **kwargs)

    # logging functions also exposed by `logging.Logger`

    def debug(self, message, *args, **kwargs):
        """debug(message, *args, **kwargs)

        Logs a debug message.
        """
        self._log(logging.DEBUG, message, args, kwargs, 'debug')

    def info(self, message, *args, **kwargs):
        """info(message, *args, **kwargs)

        Logs an info message.
        """
        self._log(logging.INFO, message, args, kwargs, 'info')

    def warning(self, message, *args, **kwargs):
        """warning(message, *args, **kwargs)

        Logs a warning message.
        """
        self._log(logging.WARNING, message, args, kwargs, 'warning')

    def warn(self, *args, **kwargs):
        """Alias for :meth:`warning`."""
        return self.warning(*args, **kwargs)

    def error(self, message, *args, **kwargs):
        """error(message, *args, **kwargs)

        To be called outside an exception handler.

        Logs an error message, then raises a ``PwnlibException``.
        """
        self._log(logging.ERROR, message, args, kwargs, 'error')
        raise PwnlibException(message % args)

    def exception(self, message, *args, **kwargs):
        """exception(message, *args, **kwargs)

        To be called from an exception handler.

        Logs a error message, then re-raises the current exception.
        """
        kwargs["exc_info"] = 1
        self._log(logging.ERROR, message, args, kwargs, 'exception')
        raise

    def critical(self, message, *args, **kwargs):
        """critical(message, *args, **kwargs)

        Logs a critical message.
        """
        self._log(logging.CRITICAL, message, args, kwargs, 'critical')

    def log(self, level, message, *args, **kwargs):
        """log(level, message, *args, **kwargs)

        Logs a message with log level `level`.  The ``pwnlib`` formatter will
        use the default :mod:`logging` formater to format this message.
        """
        self._log(level, message, args, kwargs, None)

    def isEnabledFor(self, level):
        """isEnabledFor(level) -> bool

        See if the underlying logger is enabled for the specified level.
        """
        return self._logger.isEnabledFor(level)

    def setLevel(self, level):
        """setLevel(level)

        Set the logging level for the underlying logger.
        """
        self._logger.setLevel(level)

    def addHandler(self, handler):
        """addHandler(handler)

        Add the specified handler to the underlying logger.
        """
        self._logger.addHandler(handler)

    def removeHandler(self, handler):
        """removeHandler(handler)

        Remove the specified handler from the underlying logger.
        """
        self._logger.removeHandler(handler)

class Handler(logging.StreamHandler):
    """
    A custom handler class.  This class will report whatever
    :data:`context.log_level` is currently set to as its log level.

    If :data:`term.term_mode` is enabled log records originating from a progress
    logger will not be emitted but rather an animated progress line will be
    created.

    This handler outputs to ``sys.stderr``.

    An instance of this handler is added to the ``'pwnlib'`` logger.
    """
    @property
    def level(self):
        """
        The current log level; always equal to :data:`context.log_level`.
        Setting this property is a no-op.
        """
        return context.log_level

    @level.setter
    def level(self, _):
        pass

    def emit(self, record):
        """
        Emit a log record or create/update an animated progress logger
        depending on whether :data:`term.term_mode` is enabled.
        """
        progress = getattr(record, 'pwnlib_progress', None)

        # if the record originates from a `Progress` object and term handling
        # is enabled we can have animated spinners! so check that
        if progress is None or not term.term_mode:
            super(Handler, self).emit(record)
            return

        # yay, spinners!

        # since we want to be able to update the spinner we overwrite the
        # message type so that the formatter doesn't output a prefix symbol
        msgtype = record.pwnlib_msgtype
        record.pwnlib_msgtype = 'animated'
        msg = "%s\n" % self.format(record)

        # we enrich the `Progress` object to keep track of the spinner
        if not hasattr(progress, '_spinner_handle'):
            spinner_handle = term.output('')
            msg_handle = term.output(msg)
            stop = threading.Event()
            def spin():
                '''Wheeeee!'''
                state = 0
                states = random.choice(spinners.spinners)
                while True:
                    prefix = '[%s] ' % _spinner_style(states[state])
                    spinner_handle.update(prefix)
                    state = (state + 1) % len(states)
                    if stop.wait(0.1):
                        break
            t = Thread(target = spin)
            t.daemon = True
            t.start()
            progress._spinner_handle = spinner_handle
            progress._msg_handle = msg_handle
            progress._stop_event = stop
            progress._spinner_thread = t
        else:
            progress._msg_handle.update(msg)

        # if the message type was not a status message update, then we should
        # stop the spinner
        if msgtype != 'status':
            progress._stop_event.set()
            progress._spinner_thread.join()
            style, symb = _msgtype_prefixes[msgtype]
            prefix = '[%s] ' % style(symb)
            progress._spinner_handle.update(prefix)

class Formatter(logging.Formatter):
    """
    Logging formatter which performs custom formatting for log records
    containing the ``'pwnlib_msgtype'`` attribute.  Other records are formatted
    using the `logging` modules default formatter.

    If ``'pwnlib_msgtype'`` is set, it performs the following actions:

    * A prefix looked up in `_msgtype_prefixes` is prepended to the message.
    * The message is prefixed such that it starts on column four.
    * If the message spans multiple lines they are split, and all subsequent
      lines are indented.

    This formatter is used by the handler installed on the ``'pwnlib'`` logger.
    """

    # Indentation from the left side of the terminal.
    # All log messages will be indented at list this far.
    indent    = '    '

    # Newline, followed by an indent.  Used to wrap multiple lines.
    nlindent  = '\n' + indent

    def format(self, record):
        # use the default formatter to actually format the record
        msg = super(Formatter, self).format(record)

        # then put on a prefix symbol according to the message type

        msgtype = getattr(record, 'pwnlib_msgtype', None)

        # if 'pwnlib_msgtype' is not set (or set to `None`) we just return the
        # message as it is
        if msgtype is None:
            return msg

        if msgtype in _msgtype_prefixes:
            style, symb = _msgtype_prefixes[msgtype]
            prefix = '[%s] ' % style(symb)
        elif msgtype == 'indented':
            prefix = self.indent
        elif msgtype == 'animated':
            # the handler will take care of updating the spinner, so we will
            # not include it here
            prefix = ''
        else:
            # this should never happen
            prefix = '[?] '

        msg = prefix + msg
        msg = self.nlindent.join(msg.splitlines())
        return msg

# we keep a dictionary of loggers such that multiple calls to `getLogger` with
# the same name will return the same logger
_loggers = dict()
def getLogger(name):
    '''getLogger(name) -> Logger

    Retreives the logger named `name`.  If no such logger exists a new one will
    be instantiated using :func:`logging.getLogger`.

    This function should be used instead of :func:`logging.getLogger` as we add
    some ``pwnlib`` flavor by wrapping it in a :class:`Logger`.
    '''
    if name not in _loggers:
        # if we don't have this logger create a new one and feed it through our
        # "proxy" class
        _loggers[name] = Logger(logging.getLogger(name))
    return _loggers[name]

class LogfileHandler(logging.FileHandler):
    def __init__(self):
        super(LogfileHandler, self).__init__('/dev/null')
    @property
    def stream(self):
        return context.log_file
    @stream.setter
    def stream(self, value):
        pass
    def handle(self, *a, **kw):
        if self.stream.name != '/dev/null':
            super(LogfileHandler, self).handle(*a, **kw)

iso_8601 = '%Y-%m-%dT%H:%M:%S'
fmt      = '%(asctime)s:%(levelname)s:%(name)s:%(message)s'
log_file = LogfileHandler()
log_file.setFormatter(logging.Formatter(fmt, iso_8601))

#
# The root 'pwnlib' logger is declared here.  To change the target of all
# 'pwntools'-specific logging, only this logger needs to be changed.
#
# Logging cascades upward through the heirarchy,
# so the only point that should ever need to be
# modified is the root 'pwnlib' logger.
#
# For example:
#     map(rootlogger.removeHandler, rootlogger.handlers)
#     logger.addHandler(myCoolPitchingHandler)
#

rootlogger = getLogger('pwnlib')
# rootlogger.addHandler(log_file)

console   = Handler()
formatter = Formatter()
console.setFormatter(formatter)

def install_default_handler():
    '''install_default_handler()

    Instantiates a :class:`Handler` and :class:`Formatter` and installs them for
    the ``pwnlib`` root logger.  This function is automatically called from when
    importing :mod:`pwn`.
    '''
    console.stream = sys.stderr
    logger         = logging.getLogger('pwnlib')
    if console not in logger.handlers:
        logger.addHandler(console)
