import { Injectable, signal } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class ViewportService {
  public readonly isMobile = signal(window.innerWidth <= 1200);

  public constructor() {
    window.addEventListener('resize', () => {
      this.isMobile.set(window.innerWidth <= 1200);
    });
  }
}
