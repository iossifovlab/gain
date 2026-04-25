import { JobStatus } from '../job-creation/jobs';

export class JobNotification {
  public constructor(
    public jobId: number,
    public status: JobStatus,
  ) {}

  public static fromJson(json: object): JobNotification {
    if (!json) {
      return undefined;
    }

    return new JobNotification(
      json['job_id'] as number,
      json['status'] as JobStatus,
    );
  }
}

export type PipelineStatus = 'loaded' | 'loading' | 'unloaded';

export class PipelineNotification {
  public constructor(
    public pipelineId: string,
    public status: PipelineStatus,
  ) {}

  public static fromJson(json: object): PipelineNotification {
    if (!json) {
      return undefined;
    }

    return new PipelineNotification(
      (json['pipeline_id'] as number).toString(),
      json['status'] as PipelineStatus,
    );
  }
}