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
  // public reopenConnection(): Observable<void> {
  //   // If reconnection already in progress, return the shared observable
  //   // This allows multiple components to wait for the same reconnection
  //   // Check only isReconnecting to avoid race condition with flag assignment
  //   if (this.isReconnecting) {
  //     // pendingReconnection$ should exist, but provide fallback
  //     return this.pendingReconnection$ || of(undefined);
  //   }

  //   // Check max retry attempts BEFORE incrementing
  //   if (this.reconnectionAttempts >= this.maxReconnectionAttempts) {
  //     console.error(`Max reconnection attempts (${this.maxReconnectionAttempts}) reached.`);
  //     return throwError(() => new Error('Max reconnection attempts reached'));
  //   }

  //   // Calculate delay based on attempt count
  //   // First attempt: 200ms (allow session sync in CI)
  //   // Subsequent attempts: exponential backoff (1s, 2s, 4s, 8s, max 10s)
  //   const delayMs = this.reconnectionAttempts === 0
  //     ? 200
  //     : Math.min(1000 * Math.pow(2, this.reconnectionAttempts - 1), 10000);

  //   // Increment BEFORE creating observable so it reflects in tap()
  //   const currentAttempt = this.reconnectionAttempts;
  //   this.reconnectionAttempts++;

  //   // Create the observable FIRST (don't close old connection yet - let subscriptions continue)
  //   const reconnectObservable = timer(delayMs).pipe(
  //     tap(() => {
  //       console.log(`WebSocket reconnection attempt ${currentAttempt + 1}/${this.maxReconnectionAttempts}`);
  //       // Only clear socket on 3rd+ attempts to avoid race condition with early attempts
  //       if (currentAttempt >= 2) {
  //         this.socketNotifications = null;
  //       }
  //       this.ensureConnected();
  //       this.isReconnecting = false;
  //       this.pendingReconnection$ = null;
  //       // Don't reset attempts here - let them accumulate until socket is stable
  //     }),
  //     map(() => undefined as void), // Convert timer output to void
  //     catchError(err => {
  //       this.isReconnecting = false;
  //       this.pendingReconnection$ = null;
  //       return throwError(() => err);
  //     }),
  //     shareReplay(1) // Share the observable across multiple subscribers
  //   );

  //   // Store observable reference BEFORE setting the flag
  //   this.pendingReconnection$ = reconnectObservable;

  //   // Set flag LAST - after observable is ready - to prevent race condition
  //   this.isReconnecting = true;

  //   return reconnectObservable;
  // }

  // public resetReconnectionAttempts(): void {
  //   // Reset attempts when socket is stable (called after successful notification received)
  //   this.reconnectionAttempts = 0;
  // }
}
