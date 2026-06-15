import { Injectable, signal } from '@angular/core';
import { Observable } from 'rxjs';
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
  public readonly editorWidth = signal<number>(null);
  public readonly hideComponents = signal<boolean>(false);
  public readonly loadedWhileLoggedIn = signal<boolean>(false);

  private pipelineInfoRequestCache: Map<string, Observable<PipelineInfo>> = new Map();

  public getPendingPipelineInfoRequest(id: string): Observable<PipelineInfo> | undefined {
    return this.pipelineInfoRequestCache.get(id);
  }

  public setPendingPipelineInfoRequest(id: string, request$: Observable<PipelineInfo>): void {
    this.pipelineInfoRequestCache.set(id, request$);
  }

  public clearPipelineInfoCache(id: string): void {
    this.pipelineInfoRequestCache.delete(id);
  }
}
