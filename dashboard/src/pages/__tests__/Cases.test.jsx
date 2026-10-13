import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '../../test/test-utils'
import Cases from '../Cases'

vi.mock('../../services/api')

describe('<Cases />', () => {
  it('renders the page header without throwing', async () => {
    renderWithRouter(<Cases />)
    expect(await screen.findByRole('heading', { name: /Screening cases/i })).toBeInTheDocument()
  })
})
