import { Component, EventEmitter, OnDestroy, OnInit, Output } from '@angular/core';
import { Subscription, take } from 'rxjs';
import { SingleAnnotationService } from '../single-annotation.service';
import { CommonModule } from '@angular/common';
import { Allele } from '../single-annotation';

@Component({
  selector: 'app-alleles-table',
  imports: [CommonModule],
  templateUrl: './alleles-table.component.html',
  styleUrl: './alleles-table.component.css'
})
export class AllelesTableComponent implements OnInit, OnDestroy {
  public allelesHistory: Allele[] = [];
  private refreshAllelesSubscription = new Subscription();
  @Output() public emitAllele = new EventEmitter<string>();

  public constructor(private singleAnnotationService: SingleAnnotationService) {}

  public ngOnInit(): void {
    this.getAlleles();
    this.refreshTable();
  }

  public refreshTable(): void {
    this.refreshAllelesSubscription.unsubscribe();
    this.refreshAllelesSubscription = this.singleAnnotationService.getAllelesHistory().pipe(
    ).subscribe(alleles => {
      this.allelesHistory = alleles;
    });
  }

  private getAlleles(): void {
    this.singleAnnotationService.getAllelesHistory().pipe(take(1)).subscribe(alleles => {
      this.allelesHistory = alleles;
    });
  }

  public onDelete(alleleId: number): void {
    this.singleAnnotationService.deleteAllele(alleleId).subscribe(() => this.getAlleles());
  }

  public ngOnDestroy(): void {
    this.refreshAllelesSubscription.unsubscribe();
  }

  public makeRequest(allele: string): void {
    this.emitAllele.emit(allele);
  }
}
