import { ChangeDetectorRef, Component, DoCheck, inject, OnDestroy, OnInit } from '@angular/core';
import { Router, RouterModule, RouterOutlet } from '@angular/router';
import { UsersService } from './users.service';
import { UserData } from './users';
import { filter, distinctUntilChanged, Subscription } from 'rxjs';
import { environment } from '../../environments/environment';
import { MarkdownModule } from 'ngx-markdown';
import { SocketNotificationsService } from './socket-notifications/socket-notifications.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterModule, MarkdownModule],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
})
export class AppComponent implements DoCheck, OnInit, OnDestroy {
  public currentUserData: UserData = null;
  public readonly environment = environment;
  public menuOpen = false;
  private userDataSubscription: Subscription = new Subscription();
  // App-lifetime keep-alive subscription to the notifications socket, plus the
  // reconnect it drives. See keepSocketAlive() for why the root component --
  // not the route components -- must hold the socket open.
  private socketKeepAliveSubscription: Subscription = new Subscription();
  private reconnectionSubscription: Subscription = new Subscription();
  private firstUserDataLoad = true;

  private readonly usersService = inject(UsersService);
  private readonly changeDetectorRef = inject(ChangeDetectorRef);
  private readonly socketNotificationsService = inject(SocketNotificationsService);
  private readonly router = inject(Router);

  public ngOnInit(): void {
    this.usersService.refreshUserData();
    this.userDataSubscription = this.usersService.userData.pipe(
      filter(userData => userData !== null),
      distinctUntilChanged((a, b) => a?.email === b?.email),
    ).subscribe((userData) => {
      if (!this.firstUserDataLoad) {
        // Identity changed (login/logout): reopen the socket for the new
        // session, then re-establish the app-lifetime keep-alive on it.
        this.reconnectionSubscription.unsubscribe();
        this.reconnectionSubscription = this.socketNotificationsService.reopenConnection().subscribe({
          next: () => this.keepSocketAlive(),
          error: (e) => console.error('Failed to reopen socket:', e)
        });
      } else {
        // On the first user load, open the socket and hold it for the app's
        // lifetime (see keepSocketAlive).
        this.keepSocketAlive();
      }
      this.currentUserData = userData;
      this.firstUserDataLoad = false;
    });
  }

  /**
   * Hold a subscription to the notifications socket for the whole app session.
   *
   * The shared WebSocket is ref-counted by its subscribers. If only the route
   * components (annotation-jobs-wrapper, annotation-pipeline) subscribe, then
   * navigating to a route with no notifications consumer (e.g. About) drops the
   * last subscriber and closes the socket. For an anonymous user the backend
   * treats that last disconnect as "left the site" and deletes their completed
   * jobs and their result files (AnnotationStateConsumer.disconnect ->
   * delete_jobs), so a download link captured moments earlier then 404s.
   * Keeping this subscription on the root component -- which survives route
   * changes -- keeps the connection open across in-app navigation; it is torn
   * down only when the app itself is destroyed (tab closed). Regression guard
   * for #215.
   */
  private keepSocketAlive(): void {
    this.socketKeepAliveSubscription.unsubscribe();
    this.socketKeepAliveSubscription = this.socketNotificationsService.getJobNotifications().subscribe({
      error: (err) => {
        // A dropped socket surfaces here (CloseEvent for a graceful/unclean
        // close, Event for the abnormal-drop path). Reconnect and re-hold so
        // the connection is not left closed while sitting on a route with no
        // other consumer.
        if (err instanceof CloseEvent || err instanceof Event) {
          this.socketKeepAliveSubscription.unsubscribe();
          this.reconnectionSubscription.unsubscribe();
          this.reconnectionSubscription = this.socketNotificationsService.reopenConnection().subscribe({
            next: () => this.keepSocketAlive(),
            error: (e) => console.error('Failed to reopen socket:', e)
          });
        }
      }
    });
  }

  public ngDoCheck(): void {
    this.changeDetectorRef.detectChanges();
  }

  public ngOnDestroy(): void {
    this.userDataSubscription.unsubscribe();
    this.socketKeepAliveSubscription.unsubscribe();
    this.reconnectionSubscription.unsubscribe();
  }

  public logout(): void {
    this.usersService.logout().subscribe(() => {
      this.currentUserData = null;
      window.location.reload();
      this.usersService.refreshUserData();
    });
  }

  public login(): void {
    this.router.navigate(['/login']);
  }

  public register(): void {
    this.router.navigate(['/register']);
  }

  public isAppHeaderVisible(): boolean {
    return !this.router.url.includes('login') && !this.router.url.includes('register');
  }
}
