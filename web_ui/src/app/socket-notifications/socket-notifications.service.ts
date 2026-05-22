import { Injectable } from '@angular/core';
import { BehaviorSubject, catchError, filter, map, Observable, repeat, switchMap, throwError, timer } from 'rxjs';
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

  private retryAfterError<T>(): (source: Observable<T>) => Observable<T> {
    return source => source.pipe(
      catchError((err: unknown, caught) => {
        if (err instanceof CloseEvent) {
          return caught;
        }
        if (err instanceof Event) {
          return timer(2000).pipe(switchMap(() => caught));
        }
        return throwError(() => err as Error);
      }),
      repeat()
    );
  }

  public getJobNotifications(): Observable<JobNotification> {
    return this.socket$.pipe(
      switchMap(ws => ws.pipe(
        filter(n => n['type'] === 'job_status'),
        map((n: object) => JobNotification.fromJson(n)),
        this.retryAfterError()
      ))
    );
  }

  public getPipelineNotifications(): Observable<PipelineNotification> {
    return this.socket$.pipe(
      switchMap(ws => ws.pipe(
        filter(n => n['type'] === 'pipeline_status'),
        map((n: object) => PipelineNotification.fromJson(n)),
        this.retryAfterError()
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
