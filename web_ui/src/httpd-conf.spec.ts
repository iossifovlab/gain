/**
 * Config-text guard for web_ui/httpd.conf (iossifovlab/gain#698).
 *
 * Apache treats a CustomLog whose format argument is an undefined
 * LogFormat nickname as a literal format string, and `httpd -t`
 * reports Syntax OK on that mistake — so every access log line
 * degrades to one literal word (iossifovlab/gain#695). These tests
 * are the docker-free tripwire for that failure class.
 *
 * The parser is deliberately minimal — a flat, line-oriented read of
 * this one file. It ignores container sections (<VirtualHost>,
 * <IfModule>) and never follows Include directives; a LogFormat is
 * "defined" wherever it appears. That is exact for the flat config we
 * ship, and structural edits that outgrow it should extend the parser.
 */
import * as fs from 'fs';
import * as path from 'path';

interface ApacheLogConfig {
  undefinedNicknames: string[];
  accessLogFormats: string[];
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

function parseApacheLogConfig(confText: string): ApacheLogConfig {
  const definedFormats = new Map<string, string>();
  const customLogFormatArgs: string[] = [];
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
      customLogFormatArgs.push(tokens[2]);
    }
  }
  // Apache resolves a CustomLog format argument as a nickname first and
  // falls back to treating it as a format string. A nickname can never
  // contain '%' (mod_log_config), so a %-less argument that resolves to
  // no LogFormat is exactly the #695 mistake.
  const undefinedNicknames: string[] = [];
  const accessLogFormats: string[] = [];
  for (const formatArg of customLogFormatArgs) {
    const defined = definedFormats.get(formatArg);
    if (defined !== undefined) {
      accessLogFormats.push(defined);
    } else if (formatArg.includes('%')) {
      accessLogFormats.push(formatArg);
    } else {
      undefinedNicknames.push(formatArg);
    }
  }
  return {
    undefinedNicknames: undefinedNicknames,
    accessLogFormats: accessLogFormats,
  };
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

    expect(parsed.accessLogFormats).toStrictEqual([
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

    expect(parsed.accessLogFormats).toStrictEqual(['%h %b']);
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
    expect(parsed.accessLogFormats).toStrictEqual(['%h%b']);
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

describe('web_ui/httpd.conf log configuration', () => {
  const confText = fs.readFileSync(
    path.resolve(__dirname, '..', 'httpd.conf'), 'utf8');
  const parsed = parseApacheLogConfig(confText);

  it('defines every LogFormat nickname its CustomLog directives use', () => {
    expect(parsed.undefinedNicknames).toStrictEqual([]);
  });

  it('configures at least one access log', () => {
    expect(parsed.accessLogFormats).not.toHaveLength(0);
  });

  it('carries X-Forwarded-For in every access-log format', () => {
    // Header names are case-insensitive in Apache log formats.
    const missing = parsed.accessLogFormats.filter(
      (format) => !/%\{x-forwarded-for\}i/i.test(format));

    expect(missing).toStrictEqual([]);
  });
});
