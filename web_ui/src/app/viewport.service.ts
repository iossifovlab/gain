import { Injectable, signal } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class ViewportService {
  public readonly isMobile = signal(window.innerWidth <= 599);

  public constructor() {
    window.addEventListener('resize', () => {
      this.isMobile.set(window.innerWidth <= 599);
    });
  }
}
