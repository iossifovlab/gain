import { Component, OnInit } from '@angular/core';
import { UsersService } from '../users.service';
import { take } from 'rxjs';
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
  public constructor(
    private userService: UsersService
  ) {}

  public ngOnInit(): void {
    this.userService.getQuotas().pipe(take(1)).subscribe(quotas => {
      this.quotas = quotas;
    });

    this.userService.userData.pipe(
    ).subscribe((userData) => {
      this.isUserLoggedIn = userData.loggedIn;
    });
  }
}
