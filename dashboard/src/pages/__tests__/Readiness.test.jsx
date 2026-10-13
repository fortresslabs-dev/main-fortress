import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '../../test/test-utils'
import Readiness from '../Readiness'

vi.mock('../../services/api')

describe('<Readiness />', () => {
  it('renders the examination readiness header', async () => {
    renderWithRouter(<Readiness />)
    expect(await screen.findByRole('heading', { name: /Examination Readiness/i })).toBeInTheDocument()
  })
})
