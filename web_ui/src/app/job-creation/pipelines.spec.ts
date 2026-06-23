import { Pipeline } from './pipelines';

describe('Pipeline', () => {
  it('should return undefined when fromJson is called with null', () => {
    expect(Pipeline.fromJson(null)).toBeUndefined();
  });

  it('should parse the failure reason from a failed listing entry', () => {
    const result = Pipeline.fromJson({
      id: 7,
      name: 'broken',
      content: '- position_score: scores/NONEXISTENT',
      type: 'user',
      status: 'failed',
      error: 'Invalid configuration, reason: boom',
    });
    expect(result.status).toBe('failed');
    expect(result.error).toBe('Invalid configuration, reason: boom');
  });
});
