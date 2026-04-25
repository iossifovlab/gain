import { Injectable, signal } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class SingleAnnotationReportStateService {
  public readonly isFullReport = signal<boolean>(false);
}
