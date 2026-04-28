import { Injectable } from '@angular/core';
import { BehaviorSubject, catchError, filter, map, Observable, repeat, switchMap, throwError } from 'rxjs';
import { webSocket, WebSocketSubject } from 'rxjs/webSocket';
import { environment } from '../../../environments/environment';
import { JobNotification, PipelineNotification } from './socket-notifications';

@Injectable({
  providedIn: 'root'
})
export class SocketNotificationsService {
  public constructor() { }

  private readonly socketNotificationsUrl = `${environment.socketPath}/notifications`;
  private socketNotifications: WebSocketSubject<object> = webSocket(this.socketNotificationsUrl);
  private readonly socket$ = new BehaviorSubject<WebSocketSubject<object>>(this.socketNotifications);

  public getJobNotifications(): Observable<JobNotification> {
    return this.socket$.pipe(
      switchMap(ws => ws.pipe(
        filter(n => n['type'] === 'job_status'),
        map((n: object) => JobNotification.fromJson(n)),
        catchError((err: unknown, caught) => err instanceof CloseEvent ? caught : throwError(() => err)),
        repeat()
      ))
    );
  }

  public getPipelineNotifications(): Observable<PipelineNotification> {
    return this.socket$.pipe(
      switchMap(ws => ws.pipe(
        filter(n => n['type'] === 'pipeline_status'),
        map((n: object) => PipelineNotification.fromJson(n)),
        catchError((err: unknown, caught) => err instanceof CloseEvent ? caught : throwError(() => err)),
        repeat()
      ))
    );
  }

  public reopenConnection(): void {
    this.closeConnection();
    this.socketNotifications = webSocket(this.socketNotificationsUrl);
    this.socket$.next(this.socketNotifications);
  }

  public closeConnection(): void {
    this.socketNotifications?.complete();
    this.socketNotifications = null;
  }
}
