import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FileContent } from '../job-creation/jobs';
import { TextShortenPipe } from '../text-shorten.pipe';
import { MatSelectModule } from '@angular/material/select';
import { MatFormFieldModule } from '@angular/material/form-field';
import { JobsService } from '../job-creation/jobs.service';
import { Subscription, take } from 'rxjs';

@Component({
  selector: 'app-column-specifying',
  imports: [CommonModule, TextShortenPipe, MatSelectModule, MatFormFieldModule],
  templateUrl: './column-specifying.component.html',
  styleUrl: './column-specifying.component.css'
})
export class ColumnSpecifyingComponent implements OnChanges {
  public columnNames = [
    'location',
    'variant',
    'chrom',
    'pos',
    'position_end',
    'position_begin',
    'ref',
    'alt',
    'cnv_type',
    'vcf_like',
  ];

  @Input() public fileContent: FileContent;
  @Output() public emitColumns = new EventEmitter<Map<string, string>>();
  public mappedColumns = new Map<string, string>();
  public error = '';
  public annotatable = '';
  public validationSubscription = new Subscription();

  public constructor(
    private jobsService: JobsService,
  ) { }

  public ngOnChanges(): void {
    this.selectDefaultNames();
  }

  public onSelectName(selectedName: string, column: string): void {
    this.error = '';
    const newColumnName = this.getFileColumnNewName(column);
    if (selectedName === 'None' && newColumnName) {
      this.mappedColumns.delete(newColumnName);
    } else if (newColumnName) {
      this.mappedColumns.delete(newColumnName);
      this.mappedColumns.set(selectedName, column);
    } else {
      this.mappedColumns.set(selectedName, column);
    }
    this.validateColumns(
      this.fileContent.columns,
      this.mappedColumns,
    );
  }

  public getFileColumnNewName(column: string): string {
    for (const [key, value] of this.mappedColumns.entries()) {
      if (value === column) {
        return key;
      }
    }
    return null;
  }

  public validateColumns(
    fileHeader: string[],
    columnSpecification: Map<string, string>
  ): void {
    if (this.mappedColumns.size) {
      this.validationSubscription.unsubscribe();
      this.validationSubscription = this.jobsService.validateColumnSpecification(fileHeader, columnSpecification).pipe(
        take(1)
      ).subscribe(([annotatable, errorReason]) => {
        this.error = errorReason;
        this.annotatable = annotatable;
        this.emitColumns.emit(this.error === '' ? this.mappedColumns : null);
      });
    } else {
      this.error = 'No columns selected!';
    }
  }

  private selectDefaultNames(): void {
    let noSelected = true;
    this.fileContent.columns.forEach(fileHeader => {
      const lowerCasedHeader = fileHeader.toLocaleLowerCase();
      if (this.columnNames.includes(lowerCasedHeader)) {
        this.onSelectName(lowerCasedHeader, fileHeader);
        noSelected = false;
      }
    });

    if (noSelected) {
      this.error = 'No columns selected!';
    }
  }
}
