import { FileContent, Job, getStatusClassName } from './jobs';

describe('FileContent', () => {
  it('should return undefined when fromJson is called with null', () => {
    expect(FileContent.fromJson(null)).toBeUndefined();
  });

  it('should return dash for falsy cell values in preview', () => {
    const json = {
      separator: ',',
      columns: ['a', 'b'],
      preview: [{ a: 0, b: '' }]
    };
    const result = FileContent.fromJson(json);
    expect(result.rows[0]).toStrictEqual(['-', '-']);
  });

  it('should convert number cell values to string in preview', () => {
    const json = {
      separator: ',',
      columns: ['a', 'b'],
      preview: [{ a: 42, b: 'text' }]
    };
    const result = FileContent.fromJson(json);
    expect(result.rows[0]).toStrictEqual(['42', 'text']);
  });
});

describe('Job', () => {
  it('should return undefined when fromJsonArray is called with null', () => {
    expect(Job.fromJsonArray(null)).toBeUndefined();
  });

  it('should return undefined when fromJson is called with null', () => {
    expect(Job.fromJson(null)).toBeUndefined();
  });
});

describe('getStatusClassName', () => {
  it('should return correct css class for each status', () => {
    expect(getStatusClassName('waiting')).toBe('waiting-status');
    expect(getStatusClassName('in progress')).toBe('in-progress-status');
    expect(getStatusClassName('success')).toBe('success-status');
    expect(getStatusClassName('failed')).toBe('fail-status');
    expect(getStatusClassName('unknown')).toBe('');
  });
});
