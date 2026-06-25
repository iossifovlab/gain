import { Component, inject, OnInit } from '@angular/core';
import { UsersService } from '../users.service';
import { filter, take } from 'rxjs';
import { RateLimits } from '../users';
import { CommonModule, KeyValuePipe } from '@angular/common';

@Component({
  selector: 'app-user-quotas',
  imports: [CommonModule, KeyValuePipe],
  templateUrl: './user-quotas.component.html',
  styleUrl: './user-quotas.component.css',
})
export class UserQuotasComponent implements OnInit {
  public quotas: RateLimits;
  public isUserLoggedIn: boolean;
  private readonly userService = inject(UsersService);

  public ngOnInit(): void {
    this.userService.getQuotas().pipe(take(1)).subscribe(quotas => {
      this.quotas = quotas;
    });

    this.userService.userData.pipe(
      filter((userData) => userData !== null),
    ).subscribe((userData) => {
      this.isUserLoggedIn = userData.loggedIn;
    });
  }
}
