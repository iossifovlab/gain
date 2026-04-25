import { Injectable, signal } from '@angular/core';
import { Pipeline } from '../job-creation/pipelines';
import { PipelineStatus } from '../socket-notifications/socket-notifications';
import { PipelineInfo } from '../annotation-pipeline';

@Injectable({ providedIn: 'root' })
export class AnnotationPipelineStateService {
  public readonly pipelines = signal<Pipeline[]>([]);
  public readonly selectedPipelineId = signal<string>('');
  public readonly currentPipelineText = signal<string>('');
  public readonly currentTemporaryPipelineId = signal<string>('');
  public readonly currentTemporaryPipelineStatus = signal<PipelineStatus>(null);
  public readonly pipelineInfo = signal<PipelineInfo>(null);
  public readonly isConfigValid = signal<boolean>(false);
}
