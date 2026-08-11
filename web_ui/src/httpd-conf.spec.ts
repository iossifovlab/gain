/**
 * Config-text guard for web_ui/httpd.conf (iossifovlab/gain#698).
 *
 * Apache treats a CustomLog whose format argument is an undefined
 * LogFormat nickname as a literal format string, and `httpd -t`
 * reports Syntax OK on that mistake — so every access log line
 * degrades to one literal word (iossifovlab/gain#695). These tests
 * are the docker-free tripwire for that failure class.
 *
 * The second guard is iossifovlab/gain#754: the reset and confirmation
 * mails carry a single-use code in the query string, so the access log
 * must not record the query on those paths — and must still record it
 * everywhere else. "Which format applies?" stopped being a property of
 * the config once a CustomLog became conditional, so renderAccessLogLines
 * answers the question a request at a time, and the assertions read the
 * log line itself rather than the format that produced it.
 *
 * The parser is deliberately minimal — a flat, line-oriented read of
 * this one file. It ignores container sections (<VirtualHost>,
 * <IfModule>) and never follows Include directives; a LogFormat is
 * "defined" wherever it appears. That is exact for the flat config we
 * ship, and structural edits that outgrow it should extend the parser.
 *
 * Where it cannot model something it throws rather than guessing —
 * an unknown format specifier, a SetEnvIf keyed on anything but the
 * request path. A guard that silently ignored either would keep
 * passing while the log filled with codes.
 *
 * It is a model of the subset of Apache this config uses, not a second
 * Apache, and it is only as true as the last time someone checked it
 * against the real thing. That check was made for #754 by mounting this
 * config into httpd:2.4-alpine (2.4.68) and driving it with curl: the
 * redemption spellings below logged no code, an ordinary query survived
 * intact, and each request produced exactly one line. Note one known
 * divergence: %U here renders the path as written, while Apache logs the
 * decoded r->uri — deliberate, because it is the raw spelling that a
 * path-keyed condition would mishandle. Adding a specifier to the config
 * means re-checking it against a real server, not just against this file.
 */
import * as fs from 'fs';
import * as path from 'path';

/** A `CustomLog … env=X` / `env=!X` condition. */
interface LogCondition {
  variable: string;
  negated: boolean;
}

interface AccessLog {
  format: string;
  /** Unconditional when absent — the log applies to every request. */
  condition?: LogCondition;
}

/**
 * A rule that sets an environment variable when a request matches:
 * `SetEnvIf Request_URI "<pattern>" <variable>`, or the query-string
 * form `SetEnvIfExpr "%{QUERY_STRING} =~ /<pattern>/" <variable>`.
 */
interface EnvRule {
  subject: 'path' | 'query';
  pattern: string;
  variable: string;
}

interface ApacheLogConfig {
  undefinedNicknames: string[];
  accessLogs: AccessLog[];
  envRules: EnvRule[];
}

function formatsOf(parsed: ApacheLogConfig): string[] {
  return parsed.accessLogs.map((accessLog) => accessLog.format);
}

function tokenize(line: string): string[] {
  const tokens: string[] = [];
  const pattern = /"((?:\\.|[^"\\])*)"|(\S+)/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(line)) !== null) {
    if (match[1] === undefined) {
      tokens.push(match[2]);
    } else {
      // Unescape so the token is the string Apache sees, not config source.
      tokens.push(match[1].replace(/\\(.)/g, '$1'));
    }
  }
  return tokens;
}

function parseCondition(tokens: string[]): LogCondition | undefined {
  for (const token of tokens.slice(3)) {
    if (token.startsWith('expr=')) {
      // The other conditional form. It happens to fail safe today (an
      // unmodelled condition would leave two logs applying, which the
      // one-line-per-request assertions catch), but guessing is not this
      // parser's job.
      throw new Error(`unmodelled CustomLog condition ${token}`);
    }
    if (!token.startsWith('env=')) {
      continue;
    }
    const value = token.slice('env='.length);
    return {
      variable: value.replace(/^!/, ''),
      negated: value.startsWith('!'),
    };
  }
  return undefined;
}

/**
 * The one SetEnvIfExpr shape this parser models: a match of the query
 * string against a regex. Anything else — a header test, a boolean
 * combination — is not something a synthetic request here can answer.
 */
const QUERY_STRING_EXPR = /^%\{QUERY_STRING\}\s*=~\s*\/(.*)\/$/;

function parseEnvVariable(token: string, directive: string): string {
  if (token.includes('=')) {
    throw new Error(`unmodelled ${directive} assignment ${token}`);
  }
  // `!var` UNSETS the variable. Reading it as a variable literally named
  // "!var" would leave the real one unset and the guard none the wiser.
  if (token.startsWith('!')) {
    throw new Error(`unmodelled ${directive} unset ${token}`);
  }
  return token;
}

/**
 * The two directives that set an environment variable per request, and
 * where each keeps its pattern. `variablesFrom` is the token index the
 * variable list starts at — it has to travel with the arity that
 * `pattern` consumed, or the two drift apart silently.
 */
const ENV_DIRECTIVES: Record<string, {
  subject: EnvRule['subject'];
  minTokens: number;
  variablesFrom: number;
  pattern: (tokens: string[]) => string;
}> = {
  setenvif: {
    subject: 'path',
    minTokens: 4,
    variablesFrom: 3,
    pattern: (tokens) => {
      // Any other attribute — a header, Remote_Addr, Request_Method —
      // would make a log's selection depend on something a synthetic
      // request here cannot answer, and silently ignoring it would make
      // renderAccessLogLines below quietly wrong.
      if (tokens[1] !== 'Request_URI') {
        throw new Error(`unmodelled SetEnvIf attribute ${tokens[1]}`);
      }
      return tokens[2];
    },
  },
  setenvifexpr: {
    subject: 'query',
    minTokens: 3,
    variablesFrom: 2,
    pattern: (tokens) => {
      const expr = QUERY_STRING_EXPR.exec(tokens[1]);
      if (expr === null) {
        throw new Error(`unmodelled SetEnvIfExpr expression ${tokens[1]}`);
      }
      return expr[1];
    },
  },
};

function parseEnvRules(directive: string, tokens: string[]): EnvRule[] {
  const spec = ENV_DIRECTIVES[directive];
  const pattern = spec.pattern(tokens);
  // One directive may set several variables.
  return tokens.slice(spec.variablesFrom).map((token) => ({
    subject: spec.subject,
    pattern: pattern,
    variable: parseEnvVariable(token, tokens[0]),
  }));
}

function parseApacheLogConfig(confText: string): ApacheLogConfig {
  const definedFormats = new Map<string, string>();
  const customLogs: {formatArg: string; condition?: LogCondition}[] = [];
  const envRules: EnvRule[] = [];
  const joined = confText.replace(/\\\r?\n/g, ' ');
  for (const rawLine of joined.split('\n')) {
    const line = rawLine.trim();
    if (line === '' || line.startsWith('#')) {
      continue;
    }
    const tokens = tokenize(line);
    const directive = tokens[0].toLowerCase();
    if (directive === 'logformat' && tokens.length >= 3) {
      definedFormats.set(tokens[2], tokens[1]);
    } else if (directive === 'customlog' && tokens.length >= 3) {
      customLogs.push({
        formatArg: tokens[2],
        condition: parseCondition(tokens),
      });
    } else if (directive in ENV_DIRECTIVES
        && tokens.length >= ENV_DIRECTIVES[directive].minTokens) {
      envRules.push(...parseEnvRules(directive, tokens));
    }
  }
  // Apache resolves a CustomLog format argument as a nickname first and
  // falls back to treating it as a format string. A nickname can never
  // contain '%' (mod_log_config), so a %-less argument that resolves to
  // no LogFormat is exactly the #695 mistake.
  const undefinedNicknames: string[] = [];
  const accessLogs: AccessLog[] = [];
  for (const customLog of customLogs) {
    const defined = definedFormats.get(customLog.formatArg);
    if (defined !== undefined) {
      accessLogs.push({format: defined, condition: customLog.condition});
    } else if (customLog.formatArg.includes('%')) {
      accessLogs.push({
        format: customLog.formatArg, condition: customLog.condition,
      });
    } else {
      undefinedNicknames.push(customLog.formatArg);
    }
  }
  return {
    undefinedNicknames: undefinedNicknames,
    accessLogs: accessLogs,
    envRules: envRules,
  };
}

/**
 * Fixed stand-ins for the parts of a log line that carry no meaning
 * for these tests, so a rendered line is deterministic.
 */
const CLIENT_PEER = '172.17.0.1';
const FORWARDED_CLIENT = '203.0.113.7';
const REQUEST_TIME = '[11/Aug/2026:08:37:28 +0000]';
const PROTOCOL = 'HTTP/1.1';

/** A single-use verification code, in the shape the mails carry (a UUID). */
const LIVE_CODE = 'cc4f61d0-fc0e-4a73-8e38-926b7e76b84d';

interface ApacheRequest {
  method: string;
  /**
   * The path alone. Apache's %U and SetEnvIf's Request_URI both stop
   * before the query string, which is the whole point of #754 — so the
   * two are separate fields here rather than one spliced URL.
   */
  path: string;
  /** The query string, without its leading '?'. */
  query: string;
  status: number;
  bytes: number;
  forwardedFor: string;
}

function aRequest(overrides: Partial<ApacheRequest> = {}): ApacheRequest {
  return {
    method: 'GET',
    path: '/',
    query: '',
    status: 200,
    bytes: 191,
    forwardedFor: FORWARDED_CLIENT,
    ...overrides,
  };
}

function requestTarget(request: ApacheRequest): string {
  if (request.query === '') {
    return request.path;
  }
  return `${request.path}?${request.query}`;
}

function headerValue(request: ApacheRequest, name: string): string {
  // Header names are case-insensitive in Apache log formats. Only the
  // one header these formats log is modelled; a request here carries no
  // others, so answering '-' for them would be inventing a log line.
  if (name.toLowerCase() !== 'x-forwarded-for') {
    throw new Error(`unmodelled request header %{${name}}i`);
  }
  return request.forwardedFor;
}

function expandSpecifier(
  arg: string | undefined, letter: string, request: ApacheRequest,
): string {
  if (arg !== undefined) {
    if (letter === 'i') {
      return headerValue(request, arg);
    }
    throw new Error(`unmodelled log format specifier %{${arg}}${letter}`);
  }
  switch (letter) {
    case 'h':
      return CLIENT_PEER;
    case 'l':
      return '-';
    case 'u':
      return '-';
    case 't':
      return REQUEST_TIME;
    case 'r':
      return `${request.method} ${requestTarget(request)} ${PROTOCOL}`;
    case 's':
      return String(request.status);
    case 'b':
      // Apache logs '-' rather than 0 for an empty response body.
      return request.bytes === 0 ? '-' : String(request.bytes);
    case 'm':
      return request.method;
    case 'U':
      return request.path;
    case 'H':
      return PROTOCOL;
    case 'q':
      return request.query === '' ? '' : `?${request.query}`;
    default:
      // Deliberately fatal. A specifier this renderer does not model is a
      // specifier whose effect on the logged line is unknown, and silently
      // ignoring one would let a query-string-bearing format pass the
      // #754 guard below.
      throw new Error(`unmodelled log format specifier %${letter}`);
  }
}

function renderFormat(format: string, request: ApacheRequest): string {
  // Apache's own grammar is wider than this — status-code conditions
  // (%400,501{User-agent}i) and more modifiers exist. They are left
  // unparsable on purpose; see expandSpecifier's default branch.
  const specifier = /^%(?:(%)|[<>]?(?:\{([^}]*)\})?([a-zA-Z]))/;
  let rendered = '';
  let rest = format;
  while (rest !== '') {
    if (!rest.startsWith('%')) {
      rendered += rest[0];
      rest = rest.slice(1);
      continue;
    }
    const match = specifier.exec(rest);
    if (match === null) {
      throw new Error(
        `unparsable log format specifier at "${rest.slice(0, 8)}"`);
    }
    rendered += match[1] !== undefined
      ? '%'
      : expandSpecifier(match[2], match[3], request);
    rest = rest.slice(match[0].length);
  }
  return rendered;
}

/**
 * The access-log lines this config writes for one request — the
 * question #754 is actually about, which the format strings alone
 * cannot answer once a CustomLog is conditional.
 */
function renderAccessLogLines(
  confText: string, request: ApacheRequest,
): string[] {
  const parsed = parseApacheLogConfig(confText);
  const setVariables = new Set(
    parsed.envRules
      .filter((rule) => new RegExp(rule.pattern).test(
        rule.subject === 'query' ? request.query : request.path))
      .map((rule) => rule.variable));
  return parsed.accessLogs
    .filter((accessLog) => {
      if (accessLog.condition === undefined) {
        return true;
      }
      return setVariables.has(accessLog.condition.variable)
        !== accessLog.condition.negated;
    })
    .map((accessLog) => renderFormat(accessLog.format, request));
}

describe('parseApacheLogConfig', () => {
  it('reports a CustomLog nickname that no LogFormat defines', () => {
    const conf = 'CustomLog /proc/self/fd/1 common\n';

    const parsed = parseApacheLogConfig(conf);

    expect(parsed.undefinedNicknames).toStrictEqual(['common']);
  });

  it('does not report a CustomLog nickname that a LogFormat defines', () => {
    const conf = [
      'LogFormat "%h %l %u %t \\"%r\\" %>s %b" proxied',
      'CustomLog /proc/self/fd/1 proxied',
    ].join('\n');

    const parsed = parseApacheLogConfig(conf);

    expect(parsed.undefinedNicknames).toStrictEqual([]);
  });

  it('does not treat a quoted explicit CustomLog format string as a nickname', () => {
    const conf = 'CustomLog /proc/self/fd/1 "%h %l %u %t \\"%r\\" %>s %b"\n';

    const parsed = parseApacheLogConfig(conf);

    expect(parsed.undefinedNicknames).toStrictEqual([]);
  });

  it('does not let a nickname-less LogFormat (default-format form) define a nickname', () => {
    const conf = [
      'LogFormat "%h %l %u %t \\"%r\\" %>s %b"',
      'CustomLog /proc/self/fd/1 common',
    ].join('\n');

    const parsed = parseApacheLogConfig(conf);

    expect(parsed.undefinedNicknames).toStrictEqual(['common']);
  });

  it('resolves the effective format of every CustomLog, via nickname or inline', () => {
    const conf = [
      'LogFormat "%h \\"%{X-Forwarded-For}i\\"" proxied',
      'CustomLog /proc/self/fd/1 proxied',
      'CustomLog /var/log/other_log "%h %b"',
    ].join('\n');

    const parsed = parseApacheLogConfig(conf);

    expect(formatsOf(parsed)).toStrictEqual([
      '%h "%{X-Forwarded-For}i"',
      '%h %b',
    ]);
  });

  it('resolves a quoted CustomLog argument as a nickname when one is defined', () => {
    // Apache strips quotes during tokenization and still does the
    // nickname lookup, so `CustomLog ... "proxied"` uses the nickname.
    const conf = [
      'LogFormat "%h %b" proxied',
      'CustomLog /proc/self/fd/1 "proxied"',
    ].join('\n');

    const parsed = parseApacheLogConfig(conf);

    expect(formatsOf(parsed)).toStrictEqual(['%h %b']);
  });

  it('reports a quoted undefined nickname — quotes carry no meaning to Apache', () => {
    const conf = 'CustomLog /proc/self/fd/1 "common"\n';

    const parsed = parseApacheLogConfig(conf);

    expect(parsed.undefinedNicknames).toStrictEqual(['common']);
  });

  it('treats an unquoted %-bearing argument as an inline format, not a nickname', () => {
    const conf = 'CustomLog /proc/self/fd/1 %h%b\n';

    const parsed = parseApacheLogConfig(conf);

    expect(parsed.undefinedNicknames).toStrictEqual([]);
    expect(formatsOf(parsed)).toStrictEqual(['%h%b']);
  });

  it('joins backslash line-continuations before parsing directives', () => {
    const conf = [
      'LogFormat "%h %b" \\',
      '  proxied',
      'CustomLog /proc/self/fd/1 \\',
      '  common',
    ].join('\n');

    const parsed = parseApacheLogConfig(conf);

    expect(parsed.undefinedNicknames).toStrictEqual(['common']);
  });
});

const confText = fs.readFileSync(
  path.resolve(__dirname, '..', 'httpd.conf'), 'utf8');

describe('renderAccessLogLines', () => {
  it('applies an unconditional CustomLog to every request', () => {
    const conf = [
      'LogFormat "%m %U" plain',
      'CustomLog /proc/self/fd/1 plain',
    ].join('\n');

    const lines = renderAccessLogLines(conf, aRequest({path: '/anything'}));

    expect(lines).toStrictEqual(['GET /anything']);
  });

  it('refuses to render a format specifier it does not model', () => {
    // The guard's whole value is that an unrecognised specifier cannot
    // slip through as "logs nothing" — %L could be the query string for
    // all this renderer knows.
    const conf = 'CustomLog /proc/self/fd/1 "%h %L"\n';

    expect(() => renderAccessLogLines(conf, aRequest()))
      .toThrow('unmodelled log format specifier %L');
  });

  it('refuses a SetEnvIf that unsets a variable', () => {
    // `!var` unsets. Read as a variable named "!var" it would look like a
    // rule that sets something, while the log it guards quietly flipped.
    const conf = [
      'SetEnvIf Request_URI "^/api/" !code_bearing_query',
      'LogFormat "%m %U" plain',
      'CustomLog /proc/self/fd/1 plain env=code_bearing_query',
    ].join('\n');

    expect(() => renderAccessLogLines(conf, aRequest()))
      .toThrow('unmodelled SetEnvIf unset !code_bearing_query');
  });

  it('refuses a SetEnvIf that assigns a value to its variable', () => {
    const conf = [
      'SetEnvIf Request_URI "^/api/" code_bearing_query=yes',
      'LogFormat "%m %U" plain',
      'CustomLog /proc/self/fd/1 plain env=code_bearing_query',
    ].join('\n');

    expect(() => renderAccessLogLines(conf, aRequest()))
      .toThrow('unmodelled SetEnvIf assignment');
  });

  it('refuses a SetEnvIfExpr it cannot evaluate from a request', () => {
    const conf = [
      'SetEnvIfExpr "%{HTTP_USER_AGENT} =~ /curl/" is_curl',
      'LogFormat "%m %U" plain',
      'CustomLog /proc/self/fd/1 plain env=is_curl',
    ].join('\n');

    expect(() => renderAccessLogLines(conf, aRequest()))
      .toThrow('unmodelled SetEnvIfExpr expression');
  });

  it('refuses the expr= form of a CustomLog condition', () => {
    const conf = [
      'LogFormat "%m %U" plain',
      'CustomLog /proc/self/fd/1 plain "expr=%{QUERY_STRING} =~ /code=/"',
    ].join('\n');

    expect(() => renderAccessLogLines(conf, aRequest()))
      .toThrow('unmodelled CustomLog condition');
  });

  it('refuses a SetEnvIf keyed on anything but the request path', () => {
    // Selection keyed on a header cannot be answered from a path, and
    // guessing would make every assertion here quietly unfounded.
    const conf = [
      'SetEnvIf User-Agent "curl" is_curl',
      'LogFormat "%m %U" plain',
      'CustomLog /proc/self/fd/1 plain env=is_curl',
    ].join('\n');

    expect(() => renderAccessLogLines(conf, aRequest()))
      .toThrow('unmodelled SetEnvIf attribute User-Agent');
  });
});

describe('web_ui/httpd.conf access-log output', () => {
  it('logs one line per request, carrying the path and the forwarded client', () => {
    const lines = renderAccessLogLines(confText, aRequest({
      path: '/index.html',
    }));

    expect(lines).toHaveLength(1);
    expect(lines[0]).toContain('GET /index.html');
    expect(lines[0]).toContain(FORWARDED_CLIENT);
  });

  it.each([
    ['/api/reset_password'],
    ['/api/confirm_account'],
  ])('does not log the code a redemption of %s carries', (requestPath) => {
    // Selection reads the query and nothing else, which is also what
    // covers a redemption the throttle refused before the view ran — the
    // case where the logged code is certainly still live, since a refused
    // request spends nothing and confirmation codes never expire.
    const lines = renderAccessLogLines(confText, aRequest({
      path: requestPath,
      query: `code=${LIVE_CODE}`,
    }));

    expect(lines).toHaveLength(1);
    expect(lines[0]).not.toContain(LIVE_CODE);
    expect(lines[0]).toContain(requestPath);
    expect(lines[0]).toContain('<redacted>');
  });

  it.each([
    ['a trailing slash', '/api/reset_password/'],
    ['a percent-encoded letter', '/api/rese%74_password'],
    ['a doubled slash', '/api//reset_password'],
  ])('does not log the code of a redemption spelled with %s', (_, requestPath) => {
    // These are what a condition keyed on the path would miss. The
    // backend 404s them, so their code is never spent — except %74,
    // which daphne decodes back to the real route, making it a working
    // redemption link that used to log its own code.
    const lines = renderAccessLogLines(confText, aRequest({
      path: requestPath,
      query: `code=${LIVE_CODE}`,
      status: 404,
    }));

    expect(lines).toHaveLength(1);
    expect(lines[0]).not.toContain(LIVE_CODE);
  });

  it('does not log a code that is not the first query parameter', () => {
    const lines = renderAccessLogLines(confText, aRequest({
      path: '/api/reset_password',
      query: `next=/&code=${LIVE_CODE}`,
    }));

    expect(lines).toHaveLength(1);
    expect(lines[0]).not.toContain(LIVE_CODE);
  });

  it('keeps the query of a parameter that merely ends in "code"', () => {
    // The condition matches a parameter *named* code, not the letters
    // wherever they fall, or it would redact half the search endpoint.
    const lines = renderAccessLogLines(confText, aRequest({
      path: '/api/resources/search',
      query: 'q=zipcode=90210',
    }));

    expect(lines).toHaveLength(1);
    expect(lines[0]).toContain('q=zipcode=90210');
  });

  it('still logs the query string of an ordinary request', () => {
    const lines = renderAccessLogLines(confText, aRequest({
      path: '/api/resources/search',
      query: 'q=hg38&page=2',
    }));

    expect(lines).toHaveLength(1);
    expect(lines[0]).toContain('/api/resources/search?q=hg38&page=2');
  });
});

describe('web_ui/httpd.conf log configuration', () => {
  const parsed = parseApacheLogConfig(confText);

  it('defines every LogFormat nickname its CustomLog directives use', () => {
    expect(parsed.undefinedNicknames).toStrictEqual([]);
  });

  it('configures at least one access log', () => {
    expect(formatsOf(parsed)).not.toHaveLength(0);
  });

  it('carries X-Forwarded-For in every access-log format', () => {
    // Header names are case-insensitive in Apache log formats.
    const missing = formatsOf(parsed).filter(
      (format) => !/%\{x-forwarded-for\}i/i.test(format));

    expect(missing).toStrictEqual([]);
  });
});
