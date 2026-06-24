import { Component, inject, OnInit } from '@angular/core';
import { take } from 'rxjs';
import { AboutPageService } from './about-page.service';
import { MarkdownModule } from 'ngx-markdown';

@Component({
  selector: 'app-about-page',
  imports: [MarkdownModule],
  templateUrl: './about-page.component.html',
  styleUrl: './about-page.component.css',
})
export class AboutPageComponent implements OnInit {
  public content: string;
  public version: string;
  private readonly aboutPageService = inject(AboutPageService);

  public ngOnInit(): void {
    this.aboutPageService.getContent().pipe(take(1)).subscribe(content => {
      this.content = content;
    });
    this.aboutPageService.getVersion().pipe(take(1)).subscribe(({version}) => {
      this.version = version;
    });
  }
}
