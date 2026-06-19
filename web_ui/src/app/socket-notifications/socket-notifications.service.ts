import { Injectable } from '@angular/core';
// eslint-disable-next-line @stylistic/max-len
import { BehaviorSubject, catchError, filter, map, Observable, of, shareReplay, switchMap, tap, throwError, timer } from 'rxjs';
import { webSocket, WebSocketSubject } from 'rxjs/webSocket';
import { environment } from '../../../environments/environment';
import { JobNotification, PipelineNotification } from './socket-notifications';

@Injectable({
  providedIn: 'root'
})
export class SocketNotificationsService {
  public constructor() { }

  private readonly socketNotificationsUrl = `${environment.socketPath}/notifications`;
  private socketNotifications: WebSocketSubject<object> | null = null;
  private readonly socket$ = new BehaviorSubject<WebSocketSubject<object> | null>(null);
  private reconnectionAttempts = 0;
  private maxReconnectionAttempts = 10;
  private isReconnecting = false;
  private pendingReconnection$: Observable<void> | null = null;

  public ensureConnected(): void {
    if (!this.socketNotifications) {
      this.socketNotifications = webSocket(this.socketNotificationsUrl);
      this.socket$.next(this.socketNotifications);
    }
  }

  private retryAfterError<T>(): (source: Observable<T>) => Observable<T> {
    return source => source.pipe(
      catchError((err: unknown, caught) => {
        if (err instanceof CloseEvent) {
          return throwError(() => err);
        }
        if (err instanceof Event) {
          return timer(2000).pipe(switchMap(() => caught));
        }
        return throwError(() => err as Error);
      })
    );
  }

  public getJobNotifications(): Observable<JobNotification> {
    this.ensureConnected();
    return this.socket$.pipe(
      filter(ws => ws !== null),
      switchMap(ws => ws!.pipe(
        filter(n => n['type'] === 'job_status'),
        map((n: object) => JobNotification.fromJson(n)),
        this.retryAfterError()
      ))
    );
  }

  public getPipelineNotifications(): Observable<PipelineNotification> {
    this.ensureConnected();
    return this.socket$.pipe(
      filter(ws => ws !== null),
      switchMap(ws => ws!.pipe(
        filter(n => n['type'] === 'pipeline_status'),
        map((n: object) => PipelineNotification.fromJson(n)),
        this.retryAfterError()
      ))
    );
  }

  public reopenConnection(): Observable<void> {
    // If reconnection already in progress, return the shared observable
    // This allows multiple components to wait for the same reconnection
    // Check only isReconnecting to avoid race condition with flag assignment
    if (this.isReconnecting) {
      // pendingReconnection$ should exist, but provide fallback
      return this.pendingReconnection$ || of(undefined);
    }

    // Check max retry attempts
    if (this.reconnectionAttempts >= this.maxReconnectionAttempts) {
      console.error('Max reconnection attempts reached.');
      return throwError(() => new Error('Max reconnection attempts reached'));
    }

    // First reconnection attempt: immediate. Then exponential backoff: 1s, 2s, 4s, 8s, 16s, etc. (max 30s)
    const delayMs = this.reconnectionAttempts === 0
      ? 0
      : Math.min(1000 * Math.pow(2, this.reconnectionAttempts - 1), 30000);
    this.reconnectionAttempts++;

    // Create the observable FIRST (don't close old connection yet - let subscriptions continue)
    const reconnectObservable = timer(delayMs).pipe(
      tap(() => {
        // Close the old connection before creating new one
        this.closeConnection();
        // After delay, create and emit the new WebSocket
        this.ensureConnected();
        this.isReconnecting = false;
        this.pendingReconnection$ = null;
        this.reconnectionAttempts = 0;
      }),
      map(() => undefined as void), // Convert timer output to void
      catchError(err => {
        this.isReconnecting = false;
        this.pendingReconnection$ = null;
        return throwError(() => err);
      }),
      shareReplay(1) // Share the observable across multiple subscribers
    );

    // Store observable reference BEFORE setting the flag
    this.pendingReconnection$ = reconnectObservable;

    // Set flag LAST - after observable is ready - to prevent race condition
    this.isReconnecting = true;

    return reconnectObservable;
  }

  public closeConnection(): void {
    this.socketNotifications?.complete();
    this.socketNotifications = null;
  }
}
